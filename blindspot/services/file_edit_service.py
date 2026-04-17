"""Focused file-region extraction for safe edits."""

from __future__ import annotations

import os
from typing import Any, Dict

from .base_service import BaseService
from ..indexing import get_index_manager


class FileEditService(BaseService):
    """Return small, targeted regions instead of full-file dumps."""

    def _resolve_path(self, file_path: str) -> str:
        base = self.base_path
        if not base:
            manager = get_index_manager()
            base = manager.project_path if manager else None
        if not base:
            raise ValueError("Project path not set. Call set_project_path first.")

        full_path = os.path.normpath(os.path.join(base, file_path))
        real_path = os.path.realpath(full_path)
        real_base = os.path.realpath(base)
        try:
            common = os.path.commonpath([real_path, real_base])
        except ValueError as exc:
            raise ValueError(f"Path traversal blocked: {file_path}") from exc
        if common != real_base:
            raise ValueError(f"Path traversal blocked: {file_path}")
        return full_path

    def _read_file(self, full_path: str) -> str:
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(full_path, "r", encoding=encoding) as handle:
                    return handle.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode file: {full_path}")

    def _find_symbol_info(self, file_path: str, symbol: str) -> Dict[str, Any]:
        summary = get_index_manager().get_file_summary(file_path)
        if not summary:
            return {
                "status": "error",
                "message": "File not in index. Run build_deep_index first.",
            }

        for pool_key in ("functions", "methods", "classes"):
            for item in summary.get(pool_key, []):
                name = item.get("name", "")
                if name == symbol or name.endswith(f".{symbol}"):
                    start = item.get("line")
                    end = item.get("end_line")
                    if start is None:
                        return {
                            "status": "error",
                            "message": f"Symbol '{symbol}' found but line info missing",
                        }
                    return {
                        "status": "ok",
                        "start_line": start,
                        "end_line": end or start,
                    }

        return {
            "status": "error",
            "message": f"Symbol '{symbol}' not found in {file_path}",
            "available_symbols": [
                item.get("name")
                for pool_key in ("functions", "methods", "classes")
                for item in summary.get(pool_key, [])
            ],
        }

    def get_edit_region(
        self,
        file_path: str,
        symbol: str = None,
        start_line: int = None,
        end_line: int = None,
        context_lines: int = 5,
    ) -> Dict[str, Any]:
        """Return a numbered region around a symbol or line range."""
        try:
            full_path = self._resolve_path(file_path)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        if not os.path.exists(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            content = self._read_file(full_path)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        lines = content.splitlines()
        total_lines = len(lines)

        if symbol:
            symbol_info = self._find_symbol_info(file_path, symbol)
            if symbol_info.get("status") != "ok":
                return symbol_info
            target_start = symbol_info["start_line"]
            target_end = symbol_info["end_line"]
        elif start_line is not None and end_line is not None:
            target_start = max(1, start_line)
            target_end = min(total_lines, end_line)
        else:
            return {
                "status": "error",
                "message": "Provide 'symbol' or both 'start_line' and 'end_line'",
            }

        region_start = max(1, target_start - context_lines)
        region_end = min(total_lines, target_end + context_lines)
        region_lines = [f"{index + 1:4d} | {lines[index]}" for index in range(region_start - 1, region_end)]

        return {
            "status": "success",
            "file_path": file_path,
            "total_lines": total_lines,
            "region_start": region_start,
            "region_end": region_end,
            "target_start": target_start,
            "target_end": target_end,
            "content": "\n".join(region_lines),
        }
