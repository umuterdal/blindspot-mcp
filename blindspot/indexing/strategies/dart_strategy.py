"""
Dart parsing strategy using regex and scope tracking.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from .base_strategy import ParsingStrategy
from ..models import FileInfo, SymbolInfo


# Identifiers that look like calls but must not be treated as cross-file refs.
_DART_CALL_BLACKLIST: Set[str] = {
    "if", "for", "while", "switch", "catch", "return", "assert",
    "super", "this", "throw", "await", "yield", "new", "const", "final",
    "var", "print", "setState", "runApp",
}


class DartParsingStrategy(ParsingStrategy):
    """Dart-specific parsing strategy for Flutter and server-side Dart projects."""

    _CLASS_RE = re.compile(
        r"^\s*(?:abstract\s+|base\s+|sealed\s+|final\s+)?class\s+(\w+)"
        r"(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?"
    )
    _FUNCTION_RE = re.compile(
        r"^\s*(?!if\b|for\b|while\b|switch\b|catch\b|return\b)"
        r"(?:static\s+)?(?:[\w<>\?\[\],]+\s+)+(\w+)\s*\([^;\n]*\)\s*(?:async\s*)?(?:\{|=>)"
    )
    _IMPORT_RE = re.compile(r'(?:import|export|part)\s+[\'"]([^\'"]+)[\'"]')
    _CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

    def get_language_name(self) -> str:
        return "dart"

    def get_supported_extensions(self) -> List[str]:
        return [".dart"]

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse Dart source using regex-based scope tracking."""
        symbols: Dict[str, SymbolInfo] = {}
        functions: List[str] = []
        classes: List[str] = []
        methods: List[str] = []
        lines = content.splitlines()
        class_scopes = self._extract_class_scopes(lines)
        imports = [match.group(1) for match in self._IMPORT_RE.finditer(content)]

        # Map (symbol_id, start_line, end_line) so we can later scan function
        # bodies for call sites that become pending_calls.
        function_ranges: List[Tuple[str, int, int]] = []

        for scope in class_scopes:
            class_name = scope["name"]
            symbol_id = self._create_symbol_id(file_path, class_name)
            symbols[symbol_id] = SymbolInfo(
                type="class",
                file=file_path,
                line=scope["start_line"],
                end_line=scope["end_line"],
                signature=scope["signature"],
            )
            classes.append(class_name)

        for line_number, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("//"):
                continue

            function_match = self._FUNCTION_RE.match(stripped)
            if not function_match:
                continue

            function_name = function_match.group(1)
            owner = self._find_enclosing_class(class_scopes, line_number)
            if owner:
                canonical_name = f"{owner}.{function_name}"
                symbol_type = "method"
                methods.append(canonical_name)
            else:
                canonical_name = function_name
                symbol_type = "function"
                functions.append(function_name)

            symbol_id = self._create_symbol_id(file_path, canonical_name)
            if symbol_id in symbols:
                continue

            end_line = self._estimate_block_end(lines, line_number - 1)
            symbols[symbol_id] = SymbolInfo(
                type=symbol_type,
                file=file_path,
                line=line_number,
                end_line=end_line,
                signature=stripped,
            )
            function_ranges.append((symbol_id, line_number, end_line))

        pending_calls = self._collect_pending_calls(lines, function_ranges)

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(lines),
            symbols={"functions": functions, "classes": classes, "methods": methods},
            imports=imports,
            exports=list(dict.fromkeys(classes + functions)),
        )
        if pending_calls:
            file_info.pending_calls = pending_calls
        return symbols, file_info

    def _collect_pending_calls(
        self,
        lines: List[str],
        function_ranges: List[Tuple[str, int, int]],
    ) -> List[Tuple[str, str]]:
        """Scan function bodies for call-site names, emitting (caller, called)."""
        pending: List[Tuple[str, str]] = []
        seen: Set[Tuple[str, str]] = set()

        for caller_id, start_line, end_line in function_ranges:
            body_start = max(0, start_line)
            body_end = min(len(lines), end_line)
            for i in range(body_start, body_end):
                raw = lines[i]
                stripped = raw.strip()
                if not stripped or stripped.startswith("//"):
                    continue
                cleaned = self._strip_strings(raw)
                for match in self._CALL_RE.finditer(cleaned):
                    called = match.group(1)
                    if called in _DART_CALL_BLACKLIST:
                        continue
                    key = (caller_id, called)
                    if key in seen:
                        continue
                    seen.add(key)
                    pending.append(key)
        return pending

    def _extract_class_scopes(self, lines: List[str]) -> List[Dict[str, object]]:
        scopes: List[Dict[str, object]] = []
        for index, raw_line in enumerate(lines):
            match = self._CLASS_RE.match(raw_line.strip())
            if not match:
                continue

            class_name = match.group(1)
            scopes.append(
                {
                    "name": class_name,
                    "start_line": index + 1,
                    "end_line": self._estimate_block_end(lines, index),
                    "signature": raw_line.strip(),
                }
            )
        return scopes

    def _find_enclosing_class(self, scopes: List[Dict[str, object]], line_number: int) -> Optional[str]:
        enclosing: Optional[Dict[str, object]] = None
        for scope in scopes:
            start_line = int(scope["start_line"])
            end_line = int(scope["end_line"])
            if start_line < line_number <= end_line:
                if enclosing is None or start_line >= int(enclosing["start_line"]):
                    enclosing = scope
        return str(enclosing["name"]) if enclosing else None

    def _estimate_block_end(self, lines: List[str], start_index: int) -> int:
        """Estimate the end line of a declaration based on braces or arrow bodies."""
        if start_index < 0 or start_index >= len(lines):
            return start_index + 1

        start_line = lines[start_index]
        if "=>" in start_line and "{" not in start_line:
            return start_index + 1

        depth = 0
        seen_open = False
        for index in range(start_index, len(lines)):
            line = self._strip_strings(lines[index])
            open_count = line.count("{")
            close_count = line.count("}")
            if open_count:
                seen_open = True
            depth += open_count
            depth -= close_count
            if seen_open and depth <= 0:
                return index + 1
        return min(len(lines), start_index + 20)

    def _strip_strings(self, line: str) -> str:
        """Remove quoted strings so brace counting stays stable enough for heuristics."""
        return re.sub(r'".*?"|\'.*?\'', "", line)
