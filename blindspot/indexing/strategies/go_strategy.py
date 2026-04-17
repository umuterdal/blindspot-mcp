"""
Go parsing strategy using regex patterns.
"""

import re
from typing import Dict, List, Set, Tuple, Optional
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo


# Go language keywords and common built-ins that syntactically look like calls
# but must never be treated as cross-file references.
_GO_CALL_BLACKLIST: Set[str] = {
    "if", "for", "switch", "return", "defer", "go", "select",
    "append", "cap", "close", "copy", "delete", "len", "make",
    "new", "panic", "print", "println", "recover", "range",
}


class GoParsingStrategy(ParsingStrategy):
    """Go-specific parsing strategy using regex patterns."""

    def get_language_name(self) -> str:
        return "go"

    def get_supported_extensions(self) -> List[str]:
        return ['.go']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse Go file using regex patterns."""
        symbols = {}
        functions = []
        lines = content.splitlines()
        classes = []  # Go doesn't have classes, but we'll track structs/interfaces
        imports = self._extract_go_imports(lines)
        package = None

        for i, line in enumerate(lines):
            line = line.strip()

            # Package declaration
            if line.startswith('package '):
                package = line.split('package ')[1].strip()

            # Function declarations
            elif line.startswith('func '):
                func_match = re.match(r'func\s+(\w+)\s*\(', line)
                if func_match:
                    func_name = func_match.group(1)
                    docstring = self._extract_go_comment(lines, i)
                    symbol_id = self._create_symbol_id(file_path, func_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="function",
                        file=file_path,
                        line=i + 1,
                        signature=line,
                        docstring=docstring
                    )
                    functions.append(func_name)

                # Method declarations (func (receiver) methodName)
                method_match = re.match(r'func\s+\([^)]+\)\s+(\w+)\s*\(', line)
                if method_match:
                    method_name = method_match.group(1)
                    docstring = self._extract_go_comment(lines, i)
                    symbol_id = self._create_symbol_id(file_path, method_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="method",
                        file=file_path,
                        line=i + 1,
                        signature=line,
                        docstring=docstring
                    )
                    functions.append(method_name)

            # Struct declarations
            elif re.match(r'type\s+\w+\s+struct\s*\{', line):
                struct_match = re.match(r'type\s+(\w+)\s+struct', line)
                if struct_match:
                    struct_name = struct_match.group(1)
                    docstring = self._extract_go_comment(lines, i)
                    symbol_id = self._create_symbol_id(file_path, struct_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="struct",
                        file=file_path,
                        line=i + 1,
                        docstring=docstring
                    )
                    classes.append(struct_name)

            # Interface declarations
            elif re.match(r'type\s+\w+\s+interface\s*\{', line):
                interface_match = re.match(r'type\s+(\w+)\s+interface', line)
                if interface_match:
                    interface_name = interface_match.group(1)
                    docstring = self._extract_go_comment(lines, i)
                    symbol_id = self._create_symbol_id(file_path, interface_name)
                    symbols[symbol_id] = SymbolInfo(
                        type="interface",
                        file=file_path,
                        line=i + 1,
                        docstring=docstring
                    )
                    classes.append(interface_name)

        # Phase 2: collect pending_calls so cross-file references land in the
        # deep index (refs table) during the build phase.
        pending_calls = self._collect_go_pending_calls(lines, file_path)

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(lines),
            symbols={"functions": functions, "classes": classes},
            imports=imports,
            package=package
        )
        if pending_calls:
            file_info.pending_calls = pending_calls

        return symbols, file_info

    def _collect_go_pending_calls(
        self,
        lines: List[str],
        file_path: str,
    ) -> List[Tuple[str, str]]:
        """Emit (caller_symbol_id, called_short_name) pairs via brace tracking."""
        pending: List[Tuple[str, str]] = []
        seen: Set[Tuple[str, str]] = set()

        current_caller: Optional[str] = None
        depth = 0
        entered_body = False

        for raw_line in lines:
            stripped = raw_line.strip()
            clean, _ = self._strip_go_comments(raw_line, False)

            if current_caller is None:
                if stripped.startswith("func "):
                    func_name = self._extract_go_function_name(stripped)
                    if func_name:
                        current_caller = self._create_symbol_id(file_path, func_name)
                        depth = clean.count("{") - clean.count("}")
                        entered_body = depth > 0
                continue

            # Inside a function body: count braces outside strings.
            open_count = self._count_unquoted_characters(clean, "{")
            close_count = self._count_unquoted_characters(clean, "}")
            if open_count or close_count:
                if not entered_body and open_count:
                    entered_body = True
                depth += open_count - close_count

            if entered_body:
                for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean):
                    called = match.group(1)
                    if called in _GO_CALL_BLACKLIST or called == "func":
                        continue
                    key = (current_caller, called)
                    if key not in seen:
                        seen.add(key)
                        pending.append(key)

            if entered_body and depth <= 0:
                current_caller = None
                depth = 0
                entered_body = False

        return pending

    def _extract_go_function_name(self, line: str) -> Optional[str]:
        """Extract function name from Go function declaration."""
        try:
            # func functionName(...) or func (receiver) methodName(...)
            match = re.match(r'func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(', line)
            if match:
                return match.group(1)
        except Exception:
            return None
        return None

    def _extract_go_imports(self, lines: List[str]) -> List[str]:
        """Extract Go import paths, handling multi-line blocks and comments."""
        imports: List[str] = []
        in_block_comment = False
        paren_depth = 0

        for raw_line in lines:
            clean_line, in_block_comment = self._strip_go_comments(raw_line, in_block_comment)
            stripped = clean_line.strip()

            if not stripped:
                continue

            if paren_depth == 0:
                if not stripped.startswith('import '):
                    continue

                remainder = stripped[len('import '):].strip()
                if not remainder:
                    continue

                imports.extend(self._extract_string_literals(remainder))

                paren_depth = (
                    self._count_unquoted_characters(remainder, '(')
                    - self._count_unquoted_characters(remainder, ')')
                )
                if paren_depth <= 0:
                    paren_depth = 0
                continue

            imports.extend(self._extract_string_literals(clean_line))
            paren_depth += self._count_unquoted_characters(clean_line, '(')
            paren_depth -= self._count_unquoted_characters(clean_line, ')')
            if paren_depth <= 0:
                paren_depth = 0

        return imports

    def _strip_go_comments(self, line: str, in_block_comment: bool) -> Tuple[str, bool]:
        """Remove Go comments from a line while tracking block comment state."""
        result: List[str] = []
        i = 0
        length = len(line)

        while i < length:
            if in_block_comment:
                if line.startswith('*/', i):
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue

            if line.startswith('//', i):
                break

            if line.startswith('/*', i):
                in_block_comment = True
                i += 2
                continue

            result.append(line[i])
            i += 1

        return ''.join(result), in_block_comment

    def _extract_string_literals(self, line: str) -> List[str]:
        """Return string literal values found in a line (supports " and `)."""
        literals: List[str] = []
        i = 0
        length = len(line)

        while i < length:
            char = line[i]
            if char not in ('"', '`'):
                i += 1
                continue

            delimiter = char
            i += 1
            buffer: List[str] = []
            while i < length:
                current = line[i]
                if delimiter == '"':
                    if current == '\\':
                        if i + 1 < length:
                            buffer.append(line[i + 1])
                            i += 2
                            continue
                    elif current == '"':
                        literals.append(''.join(buffer))
                        i += 1
                        break
                else:  # Raw string delimited by backticks
                    if current == '`':
                        literals.append(''.join(buffer))
                        i += 1
                        break

                buffer.append(current)
                i += 1
            else:
                break

        return literals

    def _count_unquoted_characters(self, line: str, target: str) -> int:
        """Count occurrences of a character outside string literals."""
        count = 0
        i = 0
        length = len(line)
        delimiter: Optional[str] = None

        while i < length:
            char = line[i]
            if delimiter is None:
                if char in ('"', '`'):
                    delimiter = char
                elif char == target:
                    count += 1
            else:
                if delimiter == '"':
                    if char == '\\':
                        i += 2
                        continue
                    if char == '"':
                        delimiter = None
                elif delimiter == '`' and char == '`':
                    delimiter = None

            i += 1

        return count

    def _extract_go_comment(self, lines: List[str], line_index: int) -> Optional[str]:
        """Extract Go comment (docstring) from lines preceding the given line.
        
        Go documentation comments are regular comments that appear immediately before
        the declaration, with no blank line in between.
        """
        comment_lines = []
        
        # Look backwards from the line before the declaration
        i = line_index - 1
        while i >= 0:
            stripped = lines[i].strip()
            
            # Stop at empty line
            if not stripped:
                break
            
            # Single-line comment
            if stripped.startswith('//'):
                comment_text = stripped[2:].strip()
                comment_lines.insert(0, comment_text)
                i -= 1
            # Multi-line comment block
            elif stripped.startswith('/*') or stripped.endswith('*/'):
                # Handle single-line /* comment */
                if stripped.startswith('/*') and stripped.endswith('*/'):
                    comment_text = stripped[2:-2].strip()
                    comment_lines.insert(0, comment_text)
                    i -= 1
                # Handle multi-line comment block
                elif stripped.endswith('*/'):
                    # Found end of multi-line comment, collect until start
                    temp_lines = []
                    temp_lines.insert(0, stripped[:-2].strip())
                    i -= 1
                    while i >= 0:
                        temp_stripped = lines[i].strip()
                        if temp_stripped.startswith('/*'):
                            temp_lines.insert(0, temp_stripped[2:].strip())
                            comment_lines = temp_lines + comment_lines
                            i -= 1
                            break
                        else:
                            temp_lines.insert(0, temp_stripped)
                            i -= 1
                    break
                else:
                    break
            else:
                # Not a comment, stop looking
                break
        
        if comment_lines:
            # Join with newlines and clean up
            docstring = '\n'.join(comment_lines)
            return docstring if docstring else None
        
        return None

    def _extract_go_called_functions(self, line: str) -> List[str]:
        """Extract function names that are being called in this line."""
        called_functions = []

        # Find patterns like: functionName( or obj.methodName(
        patterns = [
            r'(\w+)\s*\(',  # functionName(
            r'\.(\w+)\s*\(',  # .methodName(
        ]

        for pattern in patterns:
            matches = re.findall(pattern, line)
            called_functions.extend(matches)

        return called_functions
