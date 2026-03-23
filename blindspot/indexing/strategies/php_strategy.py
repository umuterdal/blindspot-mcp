"""
PHP parsing strategy using tree-sitter - Optimized single-pass version.
Supports PHP classes, methods, functions, traits, interfaces, enums,
use statements, and Blade templates (.blade.php).
"""

import logging
from typing import Dict, List, Tuple, Optional
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)

import tree_sitter
from tree_sitter_php import language_php


def _node_text(content_bytes: bytes, node) -> str:
    """Extract text from a tree-sitter node using byte offsets."""
    return content_bytes[node.start_byte:node.end_byte].decode('utf8', errors='replace')


class PHPParsingStrategy(ParsingStrategy):
    """PHP-specific parsing strategy using tree-sitter - Single Pass Optimized."""

    def __init__(self):
        self.php_language = tree_sitter.Language(language_php())

    def get_language_name(self) -> str:
        return "php"

    def get_supported_extensions(self) -> List[str]:
        return ['.php']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse PHP file using tree-sitter with single-pass optimization."""
        symbols = {}
        functions = []
        classes = []
        imports = []
        methods_list = []
        traits = []
        interfaces = []

        symbol_lookup = {}

        parser = tree_sitter.Parser(self.php_language)
        context = PHPTraversalContext(
            content_bytes=b'',
            file_path=file_path,
            symbols=symbols,
            functions=functions,
            classes=classes,
            imports=imports,
            methods_list=methods_list,
            traits=traits,
            interfaces=interfaces,
            symbol_lookup=symbol_lookup,
        )

        try:
            content_bytes = content.encode('utf8')
            tree = parser.parse(content_bytes)
            context.content_bytes = content_bytes

            self._traverse_node(tree.root_node, context)

        except Exception as e:
            logger.warning(f"Error parsing PHP file {file_path}: {e}")

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(content.splitlines()),
            symbols={
                "functions": functions,
                "classes": classes,
                "methods": methods_list,
                "traits": traits,
                "interfaces": interfaces,
            },
            imports=imports,
            package=context.namespace,
        )

        return symbols, file_info

    def _traverse_node(
        self,
        node,
        ctx: 'PHPTraversalContext',
        current_class: Optional[str] = None,
        current_method: Optional[str] = None,
    ):
        """Single-pass traversal that extracts symbols and analyzes calls."""
        cb = ctx.content_bytes

        # Namespace declaration
        if node.type == 'namespace_definition':
            ns_name = self._get_child_text(node, 'namespace_name', cb)
            if ns_name:
                ctx.namespace = ns_name
            for child in node.children:
                self._traverse_node(child, ctx, current_class, current_method)
            return

        # Use (import) statements
        if node.type == 'namespace_use_declaration':
            self._extract_use_statements(node, ctx)
            return

        # Class declaration
        if node.type == 'class_declaration':
            name = self._get_child_text(node, 'name', cb)
            if name:
                symbol_id = self._create_symbol_id(ctx.file_path, name)
                docstring = self._get_docblock(node, cb)
                parent_class = self._get_parent_class(node, cb)
                implemented = self._get_implemented_interfaces(node, cb)

                sig_parts = [f"class {name}"]
                if parent_class:
                    sig_parts.append(f"extends {parent_class}")
                if implemented:
                    sig_parts.append(f"implements {', '.join(implemented)}")

                ctx.symbols[symbol_id] = SymbolInfo(
                    type="class", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=' '.join(sig_parts), docstring=docstring,
                )
                ctx.symbol_lookup[name] = symbol_id
                ctx.classes.append(name)

                for child in node.children:
                    self._traverse_node(child, ctx, current_class=name, current_method=current_method)
                return

        # Interface declaration
        if node.type == 'interface_declaration':
            name = self._get_child_text(node, 'name', cb)
            if name:
                symbol_id = self._create_symbol_id(ctx.file_path, name)
                ctx.symbols[symbol_id] = SymbolInfo(
                    type="interface", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=f"interface {name}", docstring=self._get_docblock(node, cb),
                )
                ctx.symbol_lookup[name] = symbol_id
                ctx.interfaces.append(name)
                for child in node.children:
                    self._traverse_node(child, ctx, current_class=name, current_method=current_method)
                return

        # Trait declaration
        if node.type == 'trait_declaration':
            name = self._get_child_text(node, 'name', cb)
            if name:
                symbol_id = self._create_symbol_id(ctx.file_path, name)
                ctx.symbols[symbol_id] = SymbolInfo(
                    type="trait", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=f"trait {name}", docstring=self._get_docblock(node, cb),
                )
                ctx.symbol_lookup[name] = symbol_id
                ctx.traits.append(name)
                for child in node.children:
                    self._traverse_node(child, ctx, current_class=name, current_method=current_method)
                return

        # Enum declaration (PHP 8.1+)
        if node.type == 'enum_declaration':
            name = self._get_child_text(node, 'name', cb)
            if name:
                symbol_id = self._create_symbol_id(ctx.file_path, name)
                ctx.symbols[symbol_id] = SymbolInfo(
                    type="enum", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=f"enum {name}", docstring=self._get_docblock(node, cb),
                )
                ctx.symbol_lookup[name] = symbol_id
                ctx.classes.append(name)
                for child in node.children:
                    self._traverse_node(child, ctx, current_class=name, current_method=current_method)
                return

        # Method declaration
        if node.type == 'method_declaration':
            name = self._get_child_text(node, 'name', cb)
            if name:
                full_name = f"{current_class}.{name}" if current_class else name
                symbol_id = self._create_symbol_id(ctx.file_path, full_name)

                ctx.symbols[symbol_id] = SymbolInfo(
                    type="method", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=self._get_signature(node, cb),
                    docstring=self._get_docblock(node, cb),
                )
                ctx.symbol_lookup[full_name] = symbol_id
                ctx.symbol_lookup[name] = symbol_id
                ctx.methods_list.append(full_name)
                ctx.functions.append(full_name)

                for child in node.children:
                    self._traverse_node(child, ctx, current_class=current_class, current_method=symbol_id)
                return

        # Function declaration (top-level)
        if node.type == 'function_definition':
            name = self._get_child_text(node, 'name', cb)
            if name:
                symbol_id = self._create_symbol_id(ctx.file_path, name)
                ctx.symbols[symbol_id] = SymbolInfo(
                    type="function", file=ctx.file_path,
                    line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    signature=self._get_signature(node, cb),
                    docstring=self._get_docblock(node, cb),
                )
                ctx.symbol_lookup[name] = symbol_id
                ctx.functions.append(name)

                for child in node.children:
                    self._traverse_node(child, ctx, current_class=current_class, current_method=symbol_id)
                return

        # Method/function calls
        if node.type in ('member_call_expression', 'scoped_call_expression', 'function_call_expression'):
            if current_method:
                called = self._get_called_name(node, cb)
                if called:
                    self._register_call(ctx, current_method, called)

        # Continue traversing children
        for child in node.children:
            self._traverse_node(child, ctx, current_class=current_class, current_method=current_method)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _get_child_text(node, child_type: str, cb: bytes) -> Optional[str]:
        """Get text of the first child matching the given type."""
        for child in node.children:
            if child.type == child_type:
                return cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
        return None

    @staticmethod
    def _get_parent_class(node, cb: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == 'base_clause':
                for sub in child.children:
                    if sub.type in ('name', 'qualified_name'):
                        return cb[sub.start_byte:sub.end_byte].decode('utf8', errors='replace')
        return None

    @staticmethod
    def _get_implemented_interfaces(node, cb: bytes) -> List[str]:
        interfaces = []
        for child in node.children:
            if child.type == 'class_interface_clause':
                for sub in child.children:
                    if sub.type in ('name', 'qualified_name'):
                        interfaces.append(cb[sub.start_byte:sub.end_byte].decode('utf8', errors='replace'))
        return interfaces

    @staticmethod
    def _get_signature(node, cb: bytes) -> str:
        """Extract signature up to opening brace."""
        text = cb[node.start_byte:node.end_byte].decode('utf8', errors='replace')
        brace = text.find('{')
        if brace > 0:
            return text[:brace].strip()
        semi = text.find(';')
        if semi > 0:
            return text[:semi].strip()
        return text.split('\n')[0].strip()

    @staticmethod
    def _get_docblock(node, cb: bytes) -> Optional[str]:
        """Extract PHPDoc block preceding a declaration."""
        prev = node.prev_named_sibling
        if prev and prev.type == 'comment':
            text = cb[prev.start_byte:prev.end_byte].decode('utf8', errors='replace').strip()
            if text.startswith('/**'):
                lines = text.split('\n')
                cleaned = []
                for line in lines:
                    line = line.strip().lstrip('/*').lstrip().rstrip('*/')
                    if line:
                        cleaned.append(line)
                return '\n'.join(cleaned) if cleaned else None
        return None

    @staticmethod
    def _extract_use_statements(node, ctx: 'PHPTraversalContext'):
        """Extract use (import) statements."""
        text = ctx.content_bytes[node.start_byte:node.end_byte].decode('utf8', errors='replace').strip()
        if text.startswith('use '):
            text = text[4:]
        text = text.rstrip(';').strip()

        if '{' in text:
            prefix = text[:text.index('{')].rstrip('\\').strip()
            inner = text[text.index('{') + 1:text.index('}')]
            for item in inner.split(','):
                item = item.strip()
                if item:
                    ctx.imports.append(f"{prefix}\\{item}")
        else:
            for item in text.split(','):
                item = item.strip()
                if ' as ' in item:
                    item = item.split(' as ')[0].strip()
                if item:
                    ctx.imports.append(item)

    @staticmethod
    def _get_called_name(node, cb: bytes) -> Optional[str]:
        """Extract called function/method name from a call expression."""
        if node.type == 'function_call_expression':
            for child in node.children:
                if child.type in ('name', 'qualified_name'):
                    return cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')

        elif node.type == 'member_call_expression':
            for child in node.children:
                if child.type == 'name':
                    return cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')

        elif node.type == 'scoped_call_expression':
            parts = []
            for child in node.children:
                if child.type == 'name':
                    parts.append(cb[child.start_byte:child.end_byte].decode('utf8', errors='replace'))
            if len(parts) >= 2:
                return f"{parts[0]}.{parts[1]}"
            elif parts:
                return parts[0]

        return None

    @staticmethod
    def _register_call(ctx: 'PHPTraversalContext', caller: str, called: str):
        if called in ctx.symbol_lookup:
            si = ctx.symbols[ctx.symbol_lookup[called]]
            if caller not in si.called_by:
                si.called_by.append(caller)
            return
        for name, sid in ctx.symbol_lookup.items():
            if name.endswith(f".{called}"):
                si = ctx.symbols[sid]
                if caller not in si.called_by:
                    si.called_by.append(caller)
                return


class PHPTraversalContext:
    """Context object for PHP single-pass traversal."""

    def __init__(self, content_bytes: bytes, file_path: str, symbols: Dict,
                 functions: List, classes: List, imports: List,
                 methods_list: List, traits: List, interfaces: List,
                 symbol_lookup: Dict):
        self.content_bytes = content_bytes
        self.file_path = file_path
        self.symbols = symbols
        self.functions = functions
        self.classes = classes
        self.imports = imports
        self.methods_list = methods_list
        self.traits = traits
        self.interfaces = interfaces
        self.symbol_lookup = symbol_lookup
        self.namespace: Optional[str] = None
