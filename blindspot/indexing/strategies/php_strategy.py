"""
PHP parsing strategy using tree-sitter - Optimized single-pass version.
Supports PHP classes, methods, functions, traits, interfaces, enums,
use statements, and Blade templates (.blade.php).

Cross-file call resolution notes
--------------------------------
PHP code is a mix of OOP and top-level scripting. To keep
``pending_calls`` emission useful on both styles, this strategy:

* Synthesizes a ``__file_scope__`` caller for every call that lives
  outside a function/method. Without this, files like Laravel
  bootstrap scripts or WordPress-style entry points never show up as
  cross-file callers even when they clearly wire services together.
* Tracks constructor-promoted DI properties
  (``__construct(private Foo $bar)``) so ``$this->bar->baz()`` resolves
  to ``Foo.baz`` instead of the unqualified ``baz`` — killing a large
  chunk of FPs on service-heavy Laravel / Symfony projects.
* Tracks simple local-variable types inside a method
  (``$x = new Foo();`` or ``$x = Foo::make();``) so the subsequent
  ``$x->bar()`` resolves to ``Foo.bar``.
* Tracks typed class property declarations (``private Foo $bar;``) and
  traditional constructor-body DI (``public function __construct(Foo $x)
  { $this->x = $x; }``) alongside the PHP 8.0+ promoted form.

FP risk: variable shadowing / reassignment is not modeled. If the same
local is reassigned to a different class, the last seen type wins.
Constructor body capture is restricted to direct ``$this->p = $param``
assignments where ``$param`` has a typed signature; opaque RHS
(factory calls, ternaries) is ignored rather than guessed.
FN risk: assignments that go through a factory or a ternary branch are
not captured; the unqualified method-name fallback still fires.
"""

import logging
from typing import Dict, List, Tuple, Optional
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)

import tree_sitter
from tree_sitter_php import language_php


# Stable sentinel used as the "callee owner" when a call originates
# outside any function/method. Kept unique and non-identifier-like so
# downstream filters can recognize it (see
# ``context_engine_service._is_generic_hook``).
FILE_SCOPE_NAME = "__file_scope__"


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
        if context.pending_calls:
            file_info.pending_calls = context.pending_calls

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

                # Capture typed property declarations before descending
                # so body assignments / member-calls can resolve them.
                self._capture_class_property_types(node, cb, ctx, name)

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

                # Capture constructor-promoted DI properties so
                # ``$this->service->method()`` can resolve to the
                # declared type later. PHP 8.0+ pattern. Also capture
                # traditional ``$this->x = $x`` body assignments for
                # legacy codebases that predate constructor promotion.
                if name == '__construct' and current_class:
                    self._capture_promoted_properties(node, cb, ctx, current_class)
                    self._capture_constructor_body_assignments(node, cb, ctx, current_class)

                for child in node.children:
                    self._traverse_node(child, ctx, current_class=current_class, current_method=symbol_id)
                return

        # Track simple local variable types inside methods/functions:
        #   $x = new Foo();
        #   $x = Foo::make();
        # Used to resolve the receiver of a subsequent ``$x->bar()``.
        if node.type == 'assignment_expression' and current_method:
            self._capture_local_type(node, cb, ctx, current_method)

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

        # Method/function calls + object instantiation
        if node.type in ('member_call_expression', 'scoped_call_expression',
                         'function_call_expression', 'object_creation_expression'):
            # Use a synthetic file-scope caller when the call is not
            # wrapped in a function/method — this is the common case
            # for Laravel bootstrap, WordPress plugins, and standalone
            # entry scripts. Without it, those edges were invisible.
            caller_id = current_method or self._ensure_file_scope_symbol(ctx)
            called = self._get_called_name(node, cb, current_class=current_class,
                                           current_method=current_method, ctx=ctx)
            if called:
                self._register_call(ctx, caller_id, called)

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

    def _get_called_name(
        self,
        node,
        cb: bytes,
        current_class: Optional[str] = None,
        current_method: Optional[str] = None,
        ctx: Optional['PHPTraversalContext'] = None,
    ) -> Optional[str]:
        """Extract called function/method name from a call/new expression.

        When ``ctx`` is supplied, receiver-type resolution is attempted
        for ``member_call_expression``: if the receiver is a tracked
        local variable or a promoted DI property, the owning class name
        is prefixed so the cross-file resolver can disambiguate
        overloads sharing a method name.
        """
        if node.type == 'function_call_expression':
            for child in node.children:
                if child.type in ('name', 'qualified_name'):
                    text = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    # Strip leading namespace separator \Foo -> Foo
                    return text.lstrip('\\').split('\\')[-1]

        elif node.type == 'member_call_expression':
            method_name: Optional[str] = None
            for child in node.children:
                if child.type == 'name':
                    method_name = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    break
            if not method_name:
                return None
            # Try to resolve receiver type when ctx is available.
            if ctx is not None:
                owner = self._resolve_member_call_receiver_type(
                    node, cb, ctx, current_class=current_class,
                    current_method=current_method,
                )
                if owner:
                    return f"{owner}.{method_name}"
            return method_name

        elif node.type == 'scoped_call_expression':
            parts = []
            for child in node.children:
                if child.type in ('name', 'qualified_name'):
                    text = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    parts.append(text.lstrip('\\').split('\\')[-1])
            if len(parts) >= 2:
                return f"{parts[0]}.{parts[1]}"
            elif parts:
                return parts[0]

        elif node.type == 'object_creation_expression':
            for child in node.children:
                if child.type in ('name', 'qualified_name'):
                    text = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    return text.lstrip('\\').split('\\')[-1]

        return None

    # ── Receiver-type resolution helpers ──────────────────────────────

    def _resolve_member_call_receiver_type(
        self,
        node,
        cb: bytes,
        ctx: 'PHPTraversalContext',
        current_class: Optional[str],
        current_method: Optional[str],
    ) -> Optional[str]:
        """Walk the receiver of a ``member_call_expression`` and return
        the owning class if we can determine it statically.

        Handles:
            * ``$local->foo()``            via local_types
            * ``$this->prop->foo()``       via class_properties
        Everything else returns ``None`` and falls back to unqualified
        method-name matching.
        """
        receiver = None
        for child in node.children:
            if child.type in ('variable_name', 'member_access_expression'):
                receiver = child
                break
        if receiver is None:
            return None

        if receiver.type == 'variable_name':
            var = self._variable_name_text(receiver, cb)
            if not var:
                return None
            if current_method and var in ctx.local_types.get(current_method, {}):
                return ctx.local_types[current_method][var]
            return None

        if receiver.type == 'member_access_expression':
            base = None
            prop = None
            for child in receiver.children:
                if child.type == 'variable_name' and base is None:
                    base = self._variable_name_text(child, cb)
                elif child.type == 'name' and prop is None:
                    prop = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
            if base == 'this' and prop and current_class:
                return ctx.class_properties.get(current_class, {}).get(prop)

        return None

    @staticmethod
    def _variable_name_text(node, cb: bytes) -> Optional[str]:
        """Strip the leading ``$`` from a ``variable_name`` node."""
        text = cb[node.start_byte:node.end_byte].decode('utf8', errors='replace').strip()
        if text.startswith('$'):
            text = text[1:]
        return text or None

    def _capture_promoted_properties(self, ctor_node, cb: bytes,
                                     ctx: 'PHPTraversalContext',
                                     class_name: str) -> None:
        """Extract ``__construct(private Foo $bar)`` pairs into
        ``ctx.class_properties[class_name]``. Silently skips untyped or
        non-promoted parameters.
        """
        props = ctx.class_properties.setdefault(class_name, {})
        for child in ctor_node.children:
            if child.type != 'formal_parameters':
                continue
            for param in child.children:
                if param.type not in ('property_promotion_parameter', 'simple_parameter'):
                    continue
                has_visibility = False
                type_name: Optional[str] = None
                prop_name: Optional[str] = None
                for sub in param.children:
                    if sub.type == 'visibility_modifier':
                        has_visibility = True
                    elif sub.type in ('named_type', 'primitive_type', 'qualified_name', 'name'):
                        if type_name is None:
                            text = cb[sub.start_byte:sub.end_byte].decode('utf8', errors='replace')
                            type_name = text.lstrip('?').lstrip('\\').split('\\')[-1].strip()
                    elif sub.type == 'variable_name':
                        prop_name = self._variable_name_text(sub, cb)
                if has_visibility and prop_name and type_name:
                    props[prop_name] = type_name

    def _capture_class_property_types(self, class_node, cb: bytes,
                                      ctx: 'PHPTraversalContext',
                                      class_name: str) -> None:
        """Record typed ``property_declaration`` entries at class scope.

        Covers the legacy, non-promoted form ``private Foo $bar;`` so
        downstream ``$this->bar->baz()`` resolves to ``Foo.baz`` even
        when the project predates PHP 8.0 constructor promotion.
        Existing entries (e.g. set by promotion) are preserved — this
        method only fills in missing properties.
        """
        props = ctx.class_properties.setdefault(class_name, {})
        for child in class_node.children:
            if child.type != 'declaration_list':
                continue
            for member in child.children:
                if member.type != 'property_declaration':
                    continue
                type_name: Optional[str] = None
                for sub in member.children:
                    if sub.type in ('named_type', 'primitive_type', 'qualified_name'):
                        text = cb[sub.start_byte:sub.end_byte].decode('utf8', errors='replace')
                        type_name = text.lstrip('?').lstrip('\\').split('\\')[-1].strip()
                        break
                if not type_name:
                    continue
                for sub in member.children:
                    if sub.type != 'property_element':
                        continue
                    for leaf in sub.children:
                        if leaf.type == 'variable_name':
                            prop_name = self._variable_name_text(leaf, cb)
                            if prop_name and prop_name not in props:
                                props[prop_name] = type_name

    def _capture_constructor_body_assignments(self, ctor_node, cb: bytes,
                                              ctx: 'PHPTraversalContext',
                                              class_name: str) -> None:
        """Record ``$this->prop = $param`` pairs inside a ctor body.

        Only typed formal parameters flow through. Opaque RHS
        expressions (factories, ternaries, chained calls) are ignored
        rather than guessed to avoid false-positive type links.
        """
        param_types: Dict[str, str] = {}
        body_node = None
        for child in ctor_node.children:
            if child.type == 'formal_parameters':
                for param in child.children:
                    if param.type != 'simple_parameter':
                        continue
                    type_name: Optional[str] = None
                    var_name: Optional[str] = None
                    for sub in param.children:
                        if sub.type in ('named_type', 'primitive_type', 'qualified_name'):
                            if type_name is None:
                                text = cb[sub.start_byte:sub.end_byte].decode('utf8', errors='replace')
                                type_name = text.lstrip('?').lstrip('\\').split('\\')[-1].strip()
                        elif sub.type == 'variable_name':
                            var_name = self._variable_name_text(sub, cb)
                    if var_name and type_name:
                        param_types[var_name] = type_name
            elif child.type == 'compound_statement':
                body_node = child

        if not body_node or not param_types:
            return

        props = ctx.class_properties.setdefault(class_name, {})
        for stmt in body_node.children:
            if stmt.type != 'expression_statement':
                continue
            for expr in stmt.children:
                if expr.type != 'assignment_expression':
                    continue
                named = [c for c in expr.children if c.is_named]
                if len(named) < 2:
                    continue
                lhs, rhs = named[0], named[-1]
                if lhs.type != 'member_access_expression' or rhs.type != 'variable_name':
                    continue
                base: Optional[str] = None
                prop: Optional[str] = None
                for leaf in lhs.children:
                    if leaf.type == 'variable_name' and base is None:
                        base = self._variable_name_text(leaf, cb)
                    elif leaf.type == 'name' and prop is None:
                        prop = cb[leaf.start_byte:leaf.end_byte].decode('utf8', errors='replace')
                if base != 'this' or not prop:
                    continue
                rhs_var = self._variable_name_text(rhs, cb)
                if not rhs_var:
                    continue
                type_name = param_types.get(rhs_var)
                if type_name and prop not in props:
                    props[prop] = type_name

    def _capture_local_type(self, assign_node, cb: bytes,
                            ctx: 'PHPTraversalContext',
                            method_symbol_id: str) -> None:
        """Record ``$x = new Foo()`` / ``$x = Foo::make()`` locals so
        later calls on ``$x`` can resolve to ``Foo``.
        """
        left = None
        right = None
        # assignment_expression is binary: first named child is LHS, last is RHS.
        named = [c for c in assign_node.children if c.is_named]
        if len(named) >= 2:
            left, right = named[0], named[-1]
        if left is None or right is None or left.type != 'variable_name':
            return
        var = self._variable_name_text(left, cb)
        if not var:
            return
        type_name: Optional[str] = None
        if right.type == 'object_creation_expression':
            for child in right.children:
                if child.type in ('name', 'qualified_name'):
                    text = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    type_name = text.lstrip('\\').split('\\')[-1]
                    break
        elif right.type == 'scoped_call_expression':
            for child in right.children:
                if child.type in ('name', 'qualified_name'):
                    text = cb[child.start_byte:child.end_byte].decode('utf8', errors='replace')
                    type_name = text.lstrip('\\').split('\\')[-1]
                    break
        if type_name:
            ctx.local_types.setdefault(method_symbol_id, {})[var] = type_name

    def _ensure_file_scope_symbol(self, ctx: 'PHPTraversalContext') -> str:
        """Lazily create a file-scope pseudo-symbol for top-level calls.

        Keeps the symbols table free of ghost entries on files that do
        not actually have top-level activity.
        """
        if ctx.file_scope_symbol_id is not None:
            return ctx.file_scope_symbol_id
        symbol_id = self._create_symbol_id(ctx.file_path, FILE_SCOPE_NAME)
        ctx.symbols[symbol_id] = SymbolInfo(
            type="module", file=ctx.file_path,
            line=1, end_line=1,
            signature=f"<file scope {ctx.file_path}>",
            docstring=None,
        )
        ctx.symbol_lookup[FILE_SCOPE_NAME] = symbol_id
        ctx.file_scope_symbol_id = symbol_id
        return symbol_id

    @staticmethod
    def _register_call(ctx: 'PHPTraversalContext', caller: str, called: str):
        """Record a call edge.

        Intra-file: updates the target symbol's ``called_by`` list so the
        same-file lookup stays deterministic. Cross-file: queued as a
        ``pending_call`` so the builder can resolve it against the
        global index when populating the ``refs`` table.
        """
        if called in ctx.symbol_lookup:
            si = ctx.symbols[ctx.symbol_lookup[called]]
            if caller not in si.called_by:
                si.called_by.append(caller)
            ctx.pending_calls.append((caller, called))
            return
        for name, sid in ctx.symbol_lookup.items():
            if name.endswith(f".{called}"):
                si = ctx.symbols[sid]
                if caller not in si.called_by:
                    si.called_by.append(caller)
                ctx.pending_calls.append((caller, called))
                return
        # Unknown locally — defer entirely to the cross-file resolver.
        ctx.pending_calls.append((caller, called))


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
        self.pending_calls: List[Tuple[str, str]] = []
        # Receiver-type resolution tables:
        #   class_properties[ClassName] = {prop_name: TypeName}
        #     populated from constructor promotion.
        #   local_types[method_symbol_id] = {var_name: TypeName}
        #     populated from ``$x = new Foo()`` / ``$x = Foo::y()``.
        #   file_scope_symbol_id: lazily created on the first top-level
        #     call so files without top-level activity stay clean.
        self.class_properties: Dict[str, Dict[str, str]] = {}
        self.local_types: Dict[str, Dict[str, str]] = {}
        self.file_scope_symbol_id: Optional[str] = None
