"""
Java parsing strategy using tree-sitter - Optimized single-pass version.
"""

import logging
from typing import Dict, List, Tuple, Optional, Set
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)

import tree_sitter
from tree_sitter_java import language


class JavaParsingStrategy(ParsingStrategy):
    """Java-specific parsing strategy - Single Pass Optimized."""

    def __init__(self):
        self.java_language = tree_sitter.Language(language())

    def get_language_name(self) -> str:
        return "java"

    def get_supported_extensions(self) -> List[str]:
        return ['.java']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse Java file using tree-sitter with single-pass optimization."""
        symbols = {}
        functions = []
        classes = []
        imports = []
        package = None
        
        # Symbol lookup index for O(1) access
        symbol_lookup = {}  # name -> symbol_id mapping

        parser = tree_sitter.Parser(self.java_language)

        # Encode once and share with the parser. Tree-sitter's
        # start_byte/end_byte offsets index into this exact buffer;
        # slicing a Python str with those offsets is only correct for
        # pure-ASCII content and silently corrupts symbol names when
        # files contain non-ASCII characters. See TraversalContext.text.
        content_bytes = content.encode('utf-8')
        context = TraversalContext(
            content=content,
            content_bytes=content_bytes,
            file_path=file_path,
            symbols=symbols,
            functions=functions,
            classes=classes,
            imports=imports,
            symbol_lookup=symbol_lookup
        )

        try:
            tree = parser.parse(content_bytes)

            # Extract package info first
            for node in tree.root_node.children:
                if node.type == 'package_declaration':
                    package = self._extract_java_package(node, content_bytes)
                    break

            self._traverse_node_single_pass(tree.root_node, context)

        except Exception as e:
            logger.warning(f"Error parsing Java file {file_path}: {e}")

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(content.splitlines()),
            symbols={"functions": functions, "classes": classes},
            imports=imports,
            package=package
        )
        if context.pending_calls:
            file_info.pending_calls = context.pending_calls

        return symbols, file_info

    def _traverse_node_single_pass(self, node, context: 'TraversalContext', 
                                  current_class: Optional[str] = None,
                                  current_method: Optional[str] = None):
        """Single-pass traversal that extracts symbols and analyzes calls."""
        
        # Handle class declarations
        if node.type == 'class_declaration':
            name = self._get_java_class_name(node, context.content_bytes)
            if name:
                symbol_id = self._create_symbol_id(context.file_path, name)
                symbol_info = SymbolInfo(
                    type="class",
                    file=context.file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[name] = symbol_id
                context.classes.append(name)
                
                # Traverse class body with updated context
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_class=name, current_method=current_method)
                return
        
        # Handle method declarations
        elif node.type == 'method_declaration':
            name = self._get_java_method_name(node, context.content_bytes)
            if name:
                # Build full method name with class context
                if current_class:
                    full_name = f"{current_class}.{name}"
                else:
                    full_name = name

                symbol_id = self._create_symbol_id(context.file_path, full_name)
                symbol_info = SymbolInfo(
                    type="method",
                    file=context.file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=self._get_java_method_signature(node, context.content_bytes)
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[full_name] = symbol_id
                context.symbol_lookup[name] = symbol_id  # Also index by method name alone
                context.functions.append(full_name)
                
                # Traverse method body with updated context
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_class=current_class, 
                                                   current_method=symbol_id)
                return
        
        # Handle method invocations and constructor calls
        elif node.type in ('method_invocation', 'object_creation_expression'):
            if current_method:
                if node.type == 'method_invocation':
                    called_method = self._get_called_method_name(node, context.content_bytes)
                else:
                    called_method = self._get_constructed_type_name(node, context.content_bytes)
                if called_method:
                    resolved = False
                    # Use O(1) lookup instead of O(n) iteration
                    if called_method in context.symbol_lookup:
                        symbol_id = context.symbol_lookup[called_method]
                        symbol_info = context.symbols[symbol_id]
                        if current_method not in symbol_info.called_by:
                            symbol_info.called_by.append(current_method)
                        resolved = True
                    else:
                        # Try to find method with class prefix (intra-file)
                        for name, sid in context.symbol_lookup.items():
                            if name.endswith(f".{called_method}"):
                                symbol_info = context.symbols[sid]
                                if current_method not in symbol_info.called_by:
                                    symbol_info.called_by.append(current_method)
                                resolved = True
                                break
                    # Always queue as pending so cross-file refs get resolved
                    # against the global index when building the refs table.
                    context.pending_calls.append((current_method, called_method))
        
        # Handle import declarations
        elif node.type == 'import_declaration':
            import_text = context.text(node)
            # Extract the import path (remove 'import' keyword and semicolon)
            import_path = import_text.replace('import', '').replace(';', '').strip()
            if import_path:
                context.imports.append(import_path)
        
        # Continue traversing children for other node types
        for child in node.children:
            self._traverse_node_single_pass(child, context, current_class=current_class, 
                                           current_method=current_method)

    # Helpers below take ``content_bytes`` (the exact UTF-8 buffer fed
    # to tree-sitter) instead of the ``str`` form so that non-ASCII
    # content cannot drift the slice boundary. See TraversalContext.text
    # for the correctness rationale.

    @staticmethod
    def _slice(content_bytes: bytes, start: int, end: int) -> str:
        return content_bytes[start:end].decode('utf-8', errors='replace')

    def _get_java_class_name(self, node, content_bytes: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == 'identifier':
                return self._slice(content_bytes, child.start_byte, child.end_byte)
        return None

    def _get_java_method_name(self, node, content_bytes: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == 'identifier':
                return self._slice(content_bytes, child.start_byte, child.end_byte)
        return None

    def _get_java_method_signature(self, node, content_bytes: bytes) -> str:
        raw = self._slice(content_bytes, node.start_byte, node.end_byte)
        return raw.split('\n')[0].strip()

    def _extract_java_package(self, node, content_bytes: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == 'scoped_identifier':
                return self._slice(content_bytes, child.start_byte, child.end_byte)
        return None

    def _get_called_method_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract called method name from method invocation node.

        Tree-sitter's ``method_invocation`` structure is
        ``object . name ( arguments )``. The method name is the last
        ``identifier`` child that appears after a dot.
        """
        last_identifier = None
        for child in node.children:
            if child.type == 'identifier':
                last_identifier = child
        if last_identifier is not None:
            # If there is no dot, the identifier is the method name
            # (unqualified call). If there is a dot, the last identifier
            # is still the method name.
            return self._slice(
                content_bytes, last_identifier.start_byte, last_identifier.end_byte,
            )
        # Nested member chain: walk field_access
        for child in node.children:
            if child.type == 'field_access':
                # Rightmost identifier inside field_access is the final field
                rightmost = None
                for sub in child.children:
                    if sub.type == 'identifier':
                        rightmost = sub
                if rightmost is not None:
                    return self._slice(
                        content_bytes, rightmost.start_byte, rightmost.end_byte,
                    )
        return None

    def _get_constructed_type_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract class name from ``new ClassName(...)`` expression."""
        for child in node.children:
            if child.type in ('type_identifier', 'scoped_type_identifier', 'generic_type'):
                text = self._slice(content_bytes, child.start_byte, child.end_byte)
                # Strip any generic suffix and namespace prefix
                text = text.split('<')[0].strip()
                return text.rsplit('.', 1)[-1]
        return None


class TraversalContext:
    """Context object to pass state during single-pass traversal.

    ``content_bytes`` is the UTF-8 encoding fed to tree-sitter and must
    be the source of truth for any text extraction: all
    ``start_byte``/``end_byte`` offsets returned by the parser index
    into this buffer. Slicing the ``str`` form with those offsets is a
    correctness bug on any file containing non-ASCII characters.
    """

    def __init__(self, content: str, content_bytes: bytes, file_path: str,
                 symbols: Dict, functions: List, classes: List, imports: List,
                 symbol_lookup: Dict):
        self.content = content
        self.content_bytes = content_bytes
        self.file_path = file_path
        self.symbols = symbols
        self.functions = functions
        self.classes = classes
        self.imports = imports
        self.symbol_lookup = symbol_lookup
        self.pending_calls: List[Tuple[str, str]] = []

    def text(self, node) -> str:
        """Return the text covered by ``node`` as a Python ``str`` by
        slicing :attr:`content_bytes` with tree-sitter's byte offsets
        and decoding as UTF-8. ``errors='replace'`` keeps malformed
        byte sequences from crashing parse_file on edge-case inputs.
        """
        return self.content_bytes[node.start_byte:node.end_byte].decode(
            'utf-8', errors='replace',
        )