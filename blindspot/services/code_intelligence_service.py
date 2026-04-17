"""Minimal code-intelligence helpers for the context engine."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base_service import BaseService
from .generic_intelligence_service import GenericIntelligenceService
from ..indexing import get_index_manager
from ..tools.filesystem import FileSystemTool

MAX_SYMBOL_LINES = 150


class CodeIntelligenceService(BaseService):
    """Small file and symbol inspection helpers used by the core tools."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._filesystem_tool = FileSystemTool()
        self._generic = GenericIntelligenceService(ctx)

    def analyze_file(self, file_path: str) -> Dict[str, Any]:
        """Return the indexed summary for a file when the deep index is available."""
        self._validate_analysis_request(file_path)

        index_manager = get_index_manager()
        summary = index_manager.get_file_summary(file_path)
        if not summary:
            return {
                "status": "needs_deep_index",
                "message": "Deep index not available. Run build_deep_index first.",
                "file_path": file_path,
            }
        return summary

    def get_symbol_body(
        self,
        file_path: str,
        symbol_name: str,
        compact: bool = False,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return compact symbol metadata or a bounded source excerpt.

        When ``owner`` is supplied (for example the enclosing class name),
        owner-qualified matches (``Owner.symbol_name``) are preferred over
        bare matches so identically-named methods in the same file can be
        disambiguated.
        """
        summary = self.analyze_file(file_path)
        if summary.get("status") == "needs_deep_index":
            return summary

        symbol_info, symbol_type, match_type = self._find_symbol(summary, symbol_name, owner=owner)
        if not symbol_info:
            return {
                "status": "error",
                "message": f"Symbol '{symbol_name}' not found in file",
                "file_path": file_path,
                "symbol_name": symbol_name,
                "available_symbols": {
                    "functions": [item.get("name") for item in summary.get("functions", [])],
                    "methods": [item.get("name") for item in summary.get("methods", [])],
                    "classes": [item.get("name") for item in summary.get("classes", [])],
                },
            }

        line = symbol_info.get("line")
        end_line = symbol_info.get("end_line")
        if line is None:
            return {
                "status": "error",
                "message": "Symbol found but line information is missing",
                "file_path": file_path,
                "symbol_name": symbol_name,
            }

        response: Dict[str, Any] = {
            "status": "success",
            "compact": compact,
            "symbol_name": symbol_name,
            "canonical_name": symbol_info.get("name", symbol_name),
            "match_type": match_type,
            "type": symbol_type,
            "file_path": file_path,
            "line": line,
            "end_line": end_line,
            "signature": symbol_info.get("signature"),
            "docstring": symbol_info.get("docstring"),
            "called_by": symbol_info.get("called_by", []),
            "cross_file_callers": self._find_cross_file_callers(file_path, symbol_name),
        }

        if compact:
            return response

        full_path = self._resolve_full_path(file_path)
        try:
            content = self._filesystem_tool.read_file_content(full_path)
        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"File not found: {file_path}",
                "file_path": file_path,
                "symbol_name": symbol_name,
            }
        except ValueError as exc:
            return {
                "status": "error",
                "message": str(exc),
                "file_path": file_path,
                "symbol_name": symbol_name,
            }

        lines = content.splitlines(keepends=True)
        start_idx = max(line - 1, 0)
        end_idx = end_line if end_line else min(len(lines), start_idx + 50)
        code_lines = lines[start_idx:end_idx]
        truncated = len(code_lines) > MAX_SYMBOL_LINES
        if truncated:
            code_lines = code_lines[:MAX_SYMBOL_LINES]

        code = "".join(code_lines)
        if truncated:
            code += "\n# ... truncated, use get_edit_region for a tighter excerpt"

        response["truncated"] = truncated
        response["code"] = code
        return response

    def _validate_analysis_request(self, file_path: str) -> None:
        if self.base_path:
            self._require_valid_file_path(file_path)
            full_path = os.path.join(self.base_path, file_path)
            if not os.path.exists(full_path):
                raise ValueError(f"File does not exist: {file_path}")
            return

        if not file_path or ".." in file_path:
            raise ValueError(f"Invalid file path: {file_path}")

    def _find_symbol(
        self,
        summary: Dict[str, Any],
        symbol_name: str,
        owner: Optional[str] = None,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        owner_qualified = f"{owner}.{symbol_name}" if owner else None

        exact: Optional[tuple[Dict[str, Any], str]] = None
        owner_match: Optional[tuple[Dict[str, Any], str]] = None
        suffix: Optional[tuple[Dict[str, Any], str]] = None

        for pool_key, kind in (("functions", "function"), ("methods", "method"), ("classes", "class")):
            for item in summary.get(pool_key, []):
                name = item.get("name", "")
                if not name:
                    continue
                if name == symbol_name and exact is None:
                    exact = (item, kind)
                    continue
                if owner_qualified and name == owner_qualified and owner_match is None:
                    owner_match = (item, kind)
                    continue
                if kind != "class" and name.endswith(f".{symbol_name}") and suffix is None:
                    suffix = (item, kind)

        if owner_match:
            item, kind = owner_match
            return item, kind, "owner_qualified"
        if exact:
            item, kind = exact
            return item, kind, "exact"
        if suffix:
            item, kind = suffix
            return item, kind, "qualified_suffix"
        return None, None, None

    def _find_cross_file_callers(self, file_path: str, symbol_name: str) -> List[Dict[str, Any]]:
        refs = self._generic.find_references(
            symbol_name,
            definition_file=file_path,
        )
        if refs.get("status") != "success":
            return []

        callers: List[Dict[str, Any]] = []
        for ref in refs.get("references", []):
            caller_file = ref.get("file")
            if not caller_file or caller_file == file_path:
                continue

            usages = ref.get("usages") or []
            usage_types: List[str] = []
            seen_types: set = set()
            for usage in usages:
                u_type = usage.get("type")
                if u_type and u_type not in seen_types:
                    usage_types.append(u_type)
                    seen_types.add(u_type)

            first_usage = usages[0] if usages else {}

            callers.append(
                {
                    "file": caller_file,
                    "category": ref.get("category", "other"),
                    "count": ref.get("count", len(usages) or 1),
                    "usage_types": usage_types,
                    "line": first_usage.get("line"),
                    "text": first_usage.get("snippet"),
                    "in_symbol": first_usage.get("in_symbol"),
                }
            )
            if len(callers) >= 10:
                break
        return callers

    def _resolve_full_path(self, file_path: str) -> str:
        if self.base_path:
            return os.path.join(self.base_path, file_path)
        return file_path
