"""
Objective-C parsing strategy using regex patterns.
"""

import re
from typing import Dict, List, Tuple, Optional
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo


class ObjectiveCParsingStrategy(ParsingStrategy):
    """Objective-C parsing strategy using regex patterns."""

    def get_language_name(self) -> str:
        return "objective-c"

    def get_supported_extensions(self) -> List[str]:
        return ['.m', '.mm']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse Objective-C file using regex patterns."""
        symbols = {}
        functions = []
        classes = []
        imports = []

        lines = content.splitlines()
        current_class = None

        for i, line in enumerate(lines):
            line = line.strip()

            # Import statements
            if line.startswith('#import ') or line.startswith('#include '):
                import_match = re.search(r'#(?:import|include)\s+[<"]([^>"]+)[>"]', line)
                if import_match:
                    imports.append(import_match.group(1))

            # Interface declarations
            elif line.startswith('@interface '):
                interface_match = re.match(r'@interface\s+(\w+)', line)
                if interface_match:
                    class_name = interface_match.group(1)
                    current_class = class_name
                    symbol_id = self._create_symbol_id(file_path, class_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="class",
                        file=file_path,
                        line=i + 1
                    )
                    classes.append(class_name)

            # Implementation declarations
            elif line.startswith('@implementation '):
                impl_match = re.match(r'@implementation\s+(\w+)', line)
                if impl_match:
                    current_class = impl_match.group(1)

            # Method declarations
            elif line.startswith(('- (', '+ (')):
                method_match = re.search(r'[+-]\s*\([^)]+\)\s*(\w+)', line)
                if method_match:
                    method_name = method_match.group(1)
                    full_name = f"{current_class}.{method_name}" if current_class else method_name
                    symbol_id = self._create_symbol_id(file_path, full_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="method",
                        file=file_path,
                        line=i + 1,
                        signature=line
                    )
                    functions.append(full_name)

            # C function declarations
            elif re.match(r'\w+.*\s+\w+\s*\([^)]*\)\s*\{?', line) and not line.startswith(('if', 'for', 'while')):
                func_match = re.search(r'\s(\w+)\s*\([^)]*\)', line)
                if func_match:
                    func_name = func_match.group(1)
                    symbol_id = self._create_symbol_id(file_path, func_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="function",
                        file=file_path,
                        line=i + 1,
                        signature=line
                    )
                    functions.append(func_name)

            # End of class
            elif line == '@end':
                current_class = None

        # Phase 2: Add call relationship analysis
        self._analyze_objc_calls(content, symbols, file_path)

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(lines),
            symbols={"functions": functions, "classes": classes},
            imports=imports
        )

        return symbols, file_info

    def _analyze_objc_calls(self, content: str, symbols: Dict[str, SymbolInfo], file_path: str):
        """Analyze Objective-C method calls for relationships."""
        lines = content.splitlines()
        current_function = None

        for i, line in enumerate(lines):
            original_line = line
            line = line.strip()

            # Track current method context
            if line.startswith('- (') or line.startswith('+ ('):
                func_name = self._extract_objc_method_name(line)
                if func_name:
                    current_function = self._create_symbol_id(file_path, func_name)

            # Find method calls: [obj methodName] or functionName()
            if current_function and ('[' in line and ']' in line or ('(' in line and ')' in line)):
                called_functions = self._extract_objc_called_functions(line)
                for called_func in called_functions:
                    # Find the called function in symbols and add relationship
                    for symbol_id, symbol_info in symbols.items():
                        if called_func in symbol_id.split("::")[-1]:
                            if current_function not in symbol_info.called_by:
                                symbol_info.called_by.append(current_function)

    def _extract_objc_method_name(self, line: str) -> Optional[str]:
        """Extract method name from Objective-C method declaration."""
        try:
            # - (returnType)methodName:(params) or + (returnType)methodName
            match = re.search(r'[+-]\s*\([^)]*\)\s*(\w+)', line)
            if match:
                return match.group(1)
        except:
            pass
        return None

    def _extract_objc_called_functions(self, line: str) -> List[str]:
        """Extract method names that are being called in this line."""
        called_functions = []

        # Find patterns like: [obj methodName] or functionName(
        patterns = [
            r'\[\s*\w+\s+(\w+)\s*[\]:]',  # [obj methodName]
            r'(\w+)\s*\(',  # functionName(
        ]

        for pattern in patterns:
            matches = re.findall(pattern, line)
            called_functions.extend(matches)

        return called_functions
