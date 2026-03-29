"""
Code Intelligence Service - Business logic for code analysis and understanding.

This service handles the business logic for analyzing code files using the new
JSON-based indexing system optimized for LLM consumption.
"""

import logging
import os
import re
from typing import Dict, Any, List

from .base_service import BaseService

# Configuration for get_symbol_body (conservative for stability)
# Philosophy: Return minimal data reliably, use line numbers to drill down
MAX_SYMBOL_LINES = 150  # max lines to return for a single symbol
from ..tools.filesystem import FileSystemTool
from ..indexing import get_index_manager

logger = logging.getLogger(__name__)


class CodeIntelligenceService(BaseService):
    """
    Business service for code analysis and intelligence using JSON indexing.

    This service provides comprehensive code analysis using the optimized
    JSON-based indexing system for fast LLM-friendly responses.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        self._filesystem_tool = FileSystemTool()

    def analyze_file(self, file_path: str) -> Dict[str, Any]:
        """
        Analyze a file and return comprehensive intelligence.

        This is the main business method that orchestrates the file analysis
        workflow, choosing the best analysis strategy and providing rich
        insights about the code.

        Args:
            file_path: Path to the file to analyze (relative to project root)

        Returns:
            Dictionary with comprehensive file analysis

        Raises:
            ValueError: If file path is invalid or analysis fails
        """
        # Business validation
        self._validate_analysis_request(file_path)

        # Use the global index manager
        index_manager = get_index_manager()
        
        # Debug logging
        logger.info(f"Getting file summary for: {file_path}")
        logger.info(f"Index manager state - Project path: {index_manager.project_path}")
        logger.info(f"Index manager state - Has builder: {index_manager.index_builder is not None}")
        if index_manager.index_builder:
            logger.info(f"Index manager state - Has index: {index_manager.index_builder.in_memory_index is not None}")
        
        # Get file summary from JSON index
        summary = index_manager.get_file_summary(file_path)
        logger.info(f"Summary result: {summary is not None}")

        # If deep index isn't available yet, return a helpful hint instead of error
        if not summary:
            return {
                "status": "needs_deep_index",
                "message": "Deep index not available. Please run build_deep_index before calling get_file_summary.",
                "file_path": file_path
            }

        # Enhance Blade file summaries with structural outline
        if file_path.endswith('.blade.php') and summary:
            summary = self._enhance_blade_summary(file_path, summary)

        return summary

    def _validate_analysis_request(self, file_path: str) -> None:
        """
        Validate the file analysis request according to business rules.

        Args:
            file_path: File path to validate

        Raises:
            ValueError: If validation fails
        """
        # Business rule: Project must be set up OR auto-initialization must be possible
        if self.base_path:
            # Standard validation if project is set up in context
            self._require_valid_file_path(file_path)
            full_path = os.path.join(self.base_path, file_path)
            if not os.path.exists(full_path):
                raise ValueError(f"File does not exist: {file_path}")
        else:
            # Allow proceeding if auto-initialization might work
            # The index manager will handle project discovery
            logger.info("Project not set in context, relying on index auto-initialization")
            
            # Basic file path validation only
            if not file_path or '..' in file_path:
                raise ValueError(f"Invalid file path: {file_path}")

    def _enhance_blade_summary(self, file_path: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Add structural outline information to Blade file summaries."""
        try:
            if self.base_path:
                full_path = os.path.join(self.base_path, file_path)
            else:
                full_path = file_path

            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            lines = content.split('\n')
            outline = []

            # Track sections
            for i, line in enumerate(lines, 1):
                stripped = line.strip()

                # @extends
                m = re.match(r"@extends\(['\"]([^'\"]+)['\"]\)", stripped)
                if m:
                    outline.append({"line": i, "type": "extends", "name": m.group(1)})

                # @section
                m = re.match(r"@section\(['\"]([^'\"]+)['\"]\)", stripped)
                if m:
                    outline.append({"line": i, "type": "section", "name": m.group(1)})

                # @push
                m = re.match(r"@push\(['\"]([^'\"]+)['\"]\)", stripped)
                if m:
                    outline.append({"line": i, "type": "push", "name": m.group(1)})

                # @foreach / @for / @while
                if re.match(r"@foreach\s*\(", stripped):
                    # Extract the variable being iterated
                    iter_match = re.search(r"@foreach\s*\(\s*\$(\w+)\s+as", stripped)
                    name = f"${iter_match.group(1)}" if iter_match else "loop"
                    outline.append({"line": i, "type": "foreach", "name": name})

                # @if blocks (only major ones)
                if re.match(r"@if\s*\(", stripped) and len(stripped) < 100:
                    cond = re.search(r"@if\s*\((.+?)\)", stripped)
                    if cond:
                        outline.append({"line": i, "type": "if", "name": cond.group(1)[:60]})

                # x-data (Alpine components)
                m = re.search(r'x-data="(\w+)\(', stripped)
                if m:
                    outline.append({"line": i, "type": "alpine", "name": m.group(1)})

                m = re.search(r'x-data="(\{[^"]*\})"', stripped)
                if m:
                    props = re.findall(r'(\w+)\s*:', m.group(1))
                    outline.append({"line": i, "type": "alpine_inline", "name": ', '.join(props[:4])})

                # <form> tags
                m = re.search(r'<form\s', stripped, re.IGNORECASE)
                if m:
                    action_match = re.search(r'action="([^"]*)"', stripped)
                    method_match = re.search(r'method="([^"]*)"', stripped, re.IGNORECASE)
                    name_parts = []
                    if method_match:
                        name_parts.append(method_match.group(1).upper())
                    if action_match:
                        name_parts.append(action_match.group(1)[:40])
                    outline.append({"line": i, "type": "form", "name": ' '.join(name_parts) or "form"})

                # <script> tags
                if re.match(r'<script\b', stripped):
                    outline.append({"line": i, "type": "script", "name": "inline"})

            summary["blade_outline"] = outline

            # Named slots used in the file
            named_slots = []
            for m in re.finditer(r"<x-slot\s+name=['\"]([^'\"]+)['\"]|<x-slot:([a-zA-Z0-9_-]+)", content):
                name = m.group(1) or m.group(2)
                line = content[:m.start()].count('\n') + 1
                named_slots.append({"line": line, "name": name})

            # Slot conditionals (@isset/$slotName patterns)
            optional_slots = []
            for m in re.finditer(r"@isset\s*\(\s*\$(\w+)\s*\)", content):
                name = m.group(1)
                if name != 'slot':  # $slot is the default slot
                    line = content[:m.start()].count('\n') + 1
                    optional_slots.append({"line": line, "name": name})

            if named_slots:
                summary["named_slots"] = named_slots
            if optional_slots:
                summary["optional_slots"] = optional_slots

        except Exception as e:
            logger.debug(f"Blade summary enhancement failed: {e}")

        return summary

    def get_symbol_body(self, file_path: str, symbol_name: str, compact: bool = False) -> Dict[str, Any]:
        """
        Get the code body of a specific symbol from a file.

        Args:
            file_path: Path to the file containing the symbol
            symbol_name: Name of the symbol (function, method, or class)
            compact: If True, return only metadata (signature, line range, callers)
                     without the full code body. Use compact=True when you only need
                     to know WHERE a symbol is (for apply_edit), not WHAT it contains.

        Returns:
            Dictionary with symbol info. In compact mode, code is omitted.
        """
        # Get file summary from index
        index_manager = get_index_manager()
        summary = index_manager.get_file_summary(file_path)

        if not summary:
            return {
                "status": "error",
                "message": "File not found in index or deep index not built",
                "file_path": file_path,
                "symbol_name": symbol_name
            }

        # Search for the symbol in functions, methods, and classes
        symbol_info = None
        symbol_type = None

        for func in summary.get("functions", []):
            if func.get("name") == symbol_name:
                symbol_info = func
                symbol_type = "function"
                break

        if not symbol_info:
            for method in summary.get("methods", []):
                if method.get("name") == symbol_name or method.get("name", "").endswith(f".{symbol_name}"):
                    symbol_info = method
                    symbol_type = "method"
                    break

        if not symbol_info:
            for cls in summary.get("classes", []):
                if cls.get("name") == symbol_name:
                    symbol_info = cls
                    symbol_type = "class"
                    break

        if not symbol_info:
            return {
                "status": "error",
                "message": f"Symbol '{symbol_name}' not found in file",
                "file_path": file_path,
                "symbol_name": symbol_name,
                "available_symbols": {
                    "functions": [f.get("name") for f in summary.get("functions", [])],
                    "methods": [m.get("name") for m in summary.get("methods", [])],
                    "classes": [c.get("name") for c in summary.get("classes", [])]
                }
            }

        line = symbol_info.get("line")
        end_line = symbol_info.get("end_line")

        if line is None:
            return {
                "status": "error",
                "message": "Symbol found but line information is missing",
                "file_path": file_path,
                "symbol_name": symbol_name
            }

        # Compact mode: return metadata only, no code body
        if compact:
            cross_file_callers = self._find_cross_file_callers(file_path, symbol_name, symbol_type)
            return {
                "status": "success",
                "compact": True,
                "symbol_name": symbol_name,
                "type": symbol_type,
                "file_path": file_path,
                "line": line,
                "end_line": end_line,
                "total_lines": (end_line - line + 1) if end_line else None,
                "signature": symbol_info.get("signature"),
                "docstring": symbol_info.get("docstring"),
                "called_by": symbol_info.get("called_by", []),
                "cross_file_callers": cross_file_callers,
            }

        # Full mode: read the file and extract code
        try:
            if self.base_path:
                full_path = os.path.join(self.base_path, file_path)
            else:
                full_path = file_path

            with open(full_path, 'r', encoding='utf-8') as f:
                lines = list(f)

            start_idx = line - 1
            if end_line:
                end_idx = end_line
            else:
                end_idx = min(start_idx + 50, len(lines))

            code_lines = lines[start_idx:end_idx]
            truncated = False
            if len(code_lines) > MAX_SYMBOL_LINES:
                code_lines = code_lines[:MAX_SYMBOL_LINES]
                truncated = True

            code = "".join(code_lines)
            if truncated:
                remaining = (end_idx - start_idx) - MAX_SYMBOL_LINES
                code += f"\n# ... truncated ({remaining} more lines, use get_edit_region for specific sections)"

            called_by = symbol_info.get("called_by", [])
            cross_file_callers = self._find_cross_file_callers(file_path, symbol_name, symbol_type)

            return {
                "status": "success",
                "truncated": truncated,
                "symbol_name": symbol_name,
                "type": symbol_type,
                "file_path": file_path,
                "line": line,
                "end_line": end_line,
                "code": code,
                "signature": symbol_info.get("signature"),
                "docstring": symbol_info.get("docstring"),
                "called_by": called_by,
                "cross_file_callers": cross_file_callers,
            }

        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"File not found: {file_path}",
                "file_path": file_path,
                "symbol_name": symbol_name
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error reading file: {str(e)}",
                "file_path": file_path,
                "symbol_name": symbol_name
            }

    def _find_cross_file_callers(self, file_path: str, symbol_name: str, symbol_type: str) -> List[Dict[str, Any]]:
        """
        Find cross-file callers of a symbol by scanning PHP files.

        For methods like 'ClassName.methodName', searches for '->methodName(' pattern.
        For classes, searches for 'ClassName::' and 'new ClassName' patterns.
        Returns a compact list: [{file, line, text}] capped at 10 results.
        """
        index_manager = get_index_manager()
        if not index_manager.project_path:
            return []

        base = index_manager.project_path

        # Determine the search pattern based on symbol type
        if '.' in symbol_name:
            # Method: "ClassName.methodName" -> search for ->methodName(
            method_name = symbol_name.split('.')[-1]
            search_pattern = re.compile(rf'->{re.escape(method_name)}\s*\(')
            quick_check = f'->{method_name}'
        elif symbol_type == "class":
            search_pattern = re.compile(
                rf'(?:{re.escape(symbol_name)}::|new\s+{re.escape(symbol_name)}\b|use\s+[\w\\]*{re.escape(symbol_name)}\b)'
            )
            quick_check = symbol_name
        elif symbol_type == "function":
            search_pattern = re.compile(rf'\b{re.escape(symbol_name)}\s*\(')
            quick_check = symbol_name
        else:
            return []

        callers = []
        scan_dirs = ["app", "routes", "tests"]

        for scan_dir in scan_dirs:
            full_dir = os.path.join(base, scan_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, _, files in os.walk(full_dir):
                for fname in files:
                    if not fname.endswith('.php'):
                        continue

                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, base)

                    # Skip the file that defines the symbol
                    if rel == file_path:
                        continue

                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                    except Exception:
                        continue

                    # Quick string check before regex
                    if quick_check not in content:
                        continue

                    for line_no, line_text in enumerate(content.split('\n'), 1):
                        if search_pattern.search(line_text):
                            callers.append({
                                "file": rel,
                                "line": line_no,
                                "text": line_text.strip()[:120],
                            })
                            if len(callers) >= 15:
                                return callers

        return callers

