"""
Fallback parsing strategy for unsupported languages and file types.
"""

import os
import re
from typing import Dict, List, Tuple
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo
from ...adapters.language_syntax import get_language_syntax


class FallbackParsingStrategy(ParsingStrategy):
    """Fallback parser for unsupported languages and file types."""

    def __init__(self, language_name: str = "unknown"):
        self.language_name = language_name

    def get_language_name(self) -> str:
        return self.language_name

    def get_supported_extensions(self) -> List[str]:
        return []  # Fallback supports any extension

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Best-effort parsing for languages without a specialized parser."""
        symbols = {}
        functions: List[str] = []
        classes: List[str] = []
        methods: List[str] = []
        imports: List[str] = []
        exports: List[str] = []
        lines = content.splitlines()

        syntax = None
        try:
            syntax = get_language_syntax(self.language_name)
        except Exception:
            syntax = None

        if syntax and syntax.name == self.language_name:
            imports = self._extract_imports(content, syntax)
            exports = self._extract_exports(content, syntax)

            for item in syntax.find_class_declarations(content):
                class_name = item.get("name")
                if not class_name:
                    continue
                symbol_id = self._create_symbol_id(file_path, class_name)
                symbols[symbol_id] = SymbolInfo(
                    type="class",
                    file=file_path,
                    line=int(item.get("line", 1)),
                    signature=self._extract_line(lines, int(item.get("line", 1))),
                )
                classes.append(class_name)

            for item in syntax.find_function_declarations(content):
                func_name = item.get("name")
                if not func_name:
                    continue
                symbol_id = self._create_symbol_id(file_path, func_name)
                symbol_type = "function"
                if symbol_id in symbols:
                    continue
                symbols[symbol_id] = SymbolInfo(
                    type=symbol_type,
                    file=file_path,
                    line=int(item.get("line", 1)),
                    signature=self._extract_line(lines, int(item.get("line", 1))),
                )
                functions.append(func_name)

        file_info = FileInfo(
            language=self.language_name,
            line_count=len(lines),
            symbols={"functions": functions, "classes": classes, "methods": methods},
            imports=imports,
            exports=exports,
        )

        # For document files (e.g. .md, .txt, .json), we can add a symbol representing the file itself
        if self.language_name in ['markdown', 'text', 'json', 'yaml', 'xml', 'config', 'css', 'html']:
            filename = os.path.basename(file_path)
            symbol_id = self._create_symbol_id(file_path, f"file:{filename}")
            symbols[symbol_id] = SymbolInfo(
                type="file",
                file=file_path,
                line=1,
                end_line=len(content.splitlines()),
                    signature=f"{self.language_name} file: {filename}"
            )

        return symbols, file_info

    def _extract_imports(self, content: str, syntax) -> List[str]:
        pattern = getattr(syntax, "import_pattern", None)
        if not pattern:
            return []
        imports: List[str] = []
        for match in re.finditer(pattern, content, re.MULTILINE):
            for value in match.groups():
                if value:
                    imports.append(value.strip())
                    break
        return imports[:50]

    def _extract_exports(self, content: str, syntax) -> List[str]:
        if syntax.name not in {"javascript", "typescript", "dart"}:
            return []

        exports: List[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if syntax.name in {"javascript", "typescript"}:
                match = re.match(
                    r"export\s+(?:default\s+)?(?:class|function|const|let|var|interface|type)?\s*(\w+)?",
                    stripped,
                )
                if match and match.group(1):
                    exports.append(match.group(1))
            elif syntax.name == "dart":
                match = re.match(r"(?:class|enum|mixin)\s+(\w+)", stripped)
                if match:
                    exports.append(match.group(1))
        return exports[:50]

    def _extract_line(self, lines: List[str], line_number: int) -> str:
        if line_number <= 0 or line_number > len(lines):
            return ""
        return lines[line_number - 1].strip()
