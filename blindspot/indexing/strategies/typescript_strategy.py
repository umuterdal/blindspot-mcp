"""
TypeScript parsing strategy using tree-sitter - Optimized single-pass version.

Cross-file call resolution notes
--------------------------------
TS/JS code mixes module-level scripts, class methods, and heavy DI.
To keep ``pending_calls`` emission useful across these styles, this
strategy tracks two small type tables during a single traversal:

* ``property_types[ClassName] = {prop: Type}`` — populated from both
  ``public_field_definition`` declarations (e.g. ``private svc: Svc``)
  and constructor-promoted parameters
  (e.g. ``constructor(private readonly svc: Svc) {}``). Lets
  ``this.svc.foo()`` resolve to ``Svc.foo`` instead of the unqualified
  ``foo`` — the concrete NestJS/Angular DI case.
* ``local_types[func_key] = {var: Type}`` — populated from
  ``const x = new Foo()`` / ``const x: Foo = ...``. Lets a subsequent
  ``x.bar()`` resolve to ``Foo.bar``.

FP risk: variable reassignment is not modeled (last seen type wins);
static-factory inference is intentionally disabled for TS because
``Users.findOne(id)`` is more commonly a value call than a singleton.
FN risk: ``constructor(svc: Svc) { this.svc = svc; }`` without
promotion is not captured. NestJS/Angular projects overwhelmingly use
promoted parameters so the practical gap is small.
"""

import logging
from typing import Dict, List, Tuple, Optional, Set
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)

import tree_sitter
from tree_sitter_typescript import language_typescript


class TypeScriptParsingStrategy(ParsingStrategy):
    """TypeScript-specific parsing strategy using tree-sitter - Single Pass Optimized."""

    def __init__(self):
        self.ts_language = tree_sitter.Language(language_typescript())

    def get_language_name(self) -> str:
        return "typescript"

    def get_supported_extensions(self) -> List[str]:
        return ['.ts', '.tsx']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse TypeScript file using tree-sitter with single-pass optimization."""
        symbols = {}
        functions = []
        classes = []
        imports = []
        exports = []

        # Symbol lookup index for O(1) access
        symbol_lookup = {}  # name -> symbol_id mapping
        pending_calls: List[Tuple[str, str]] = []
        pending_call_set: Set[Tuple[str, str]] = set()
        variable_scopes: List[Dict[str, str]] = [{}]

        # Encode once and share with the parser. Tree-sitter's
        # start_byte/end_byte offsets index into this exact byte buffer;
        # slicing a Python str with those offsets is only correct for
        # pure-ASCII content and silently corrupts symbol names when
        # files contain non-ASCII characters (smart quotes, em-dashes,
        # comments in any non-Latin script, etc.). See TraversalContext.text.
        content_bytes = content.encode('utf-8')
        parser = tree_sitter.Parser(self.ts_language)
        tree = parser.parse(content_bytes)

        # Single-pass traversal that handles everything
        context = TraversalContext(
            content=content,
            content_bytes=content_bytes,
            file_path=file_path,
            symbols=symbols,
            functions=functions,
            classes=classes,
            imports=imports,
            exports=exports,
            symbol_lookup=symbol_lookup,
            pending_calls=pending_calls,
            pending_call_set=pending_call_set,
            variable_scopes=variable_scopes,
        )

        self._traverse_node_single_pass(tree.root_node, context)

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(content.splitlines()),
            symbols={"functions": functions, "classes": classes},
            imports=imports,
            exports=exports
        )

        if context.pending_calls:
            file_info.pending_calls = context.pending_calls

        return symbols, file_info

    def _traverse_node_single_pass(self, node, context: 'TraversalContext',
                                  current_function: Optional[str] = None,
                                  current_class: Optional[str] = None):
        """Single-pass traversal that extracts symbols and analyzes calls."""

        node_type = node.type

        # Handle function declarations
        if node_type == 'function_declaration':
            name = self._get_function_name(node, context.content_bytes)
            if name:
                symbol_id = self._create_symbol_id(context.file_path, name)
                signature = self._get_ts_function_signature(node, context.content_bytes)
                symbol_info = SymbolInfo(
                    type="function",
                    file=context.file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=signature
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[name] = symbol_id
                context.functions.append(name)

                # Traverse function body with updated context
                func_context = f"{context.file_path}::{name}"
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_function=func_context,
                                                   current_class=current_class)
                return

        # Handle class declarations
        elif node_type == 'class_declaration':
            name = self._get_class_name(node, context.content_bytes)
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

                # Capture field types + promoted constructor params
                # before walking method bodies so any call on
                # ``this.<prop>`` inside a method resolves to its
                # declared type via ``property_types``.
                self._capture_class_property_types(node, context, name)

                # Traverse class body with updated context
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_function=current_function,
                                                   current_class=name)
                return

        # Handle interface declarations
        elif node_type == 'interface_declaration':
            name = self._get_interface_name(node, context.content_bytes)
            if name:
                symbol_id = self._create_symbol_id(context.file_path, name)
                symbol_info = SymbolInfo(
                    type="interface",
                    file=context.file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[name] = symbol_id
                context.classes.append(name)  # Group interfaces with classes

                # Traverse interface body with updated context
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_function=current_function,
                                                   current_class=name)
                return

        # Handle method definitions
        elif node_type == 'method_definition':
            method_name = self._get_method_name(node, context.content_bytes)
            if method_name and current_class:
                full_name = f"{current_class}.{method_name}"
                symbol_id = self._create_symbol_id(context.file_path, full_name)
                signature = self._get_ts_function_signature(node, context.content_bytes)
                symbol_info = SymbolInfo(
                    type="method",
                    file=context.file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=signature
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[full_name] = symbol_id
                context.symbol_lookup[method_name] = symbol_id  # Also index by method name alone
                context.functions.append(full_name)

                # Traverse method body with updated context
                method_context = f"{context.file_path}::{full_name}"
                for child in node.children:
                    self._traverse_node_single_pass(child, context, current_function=method_context,
                                                   current_class=current_class)
                return

        # Handle variable declarations that define callable exports
        elif node_type in ['lexical_declaration', 'variable_statement']:
            handled = False
            for child in node.children:
                if child.type != 'variable_declarator':
                    continue
                name_node = child.child_by_field_name('name')
                value_node = child.child_by_field_name('value')
                if not name_node or not value_node:
                    continue

                if current_function is not None:
                    # Inside a function: do not register as an exported
                    # symbol, but still capture local-variable types so
                    # subsequent ``var.method()`` calls resolve to the
                    # owning class in the cross-file ref graph.
                    self._capture_local_type(child, context, current_function)
                    continue

                # Skip destructuring patterns (array_pattern, object_pattern)
                # e.g. const [foo, setFoo] = useState(...) or const { a, b } = ...
                if name_node.type in ['array_pattern', 'object_pattern']:
                    continue

                value_type = value_node.type
                if value_type not in [
                    'arrow_function',
                    'function',
                    'function_expression',
                    'call_expression',
                    'new_expression',
                    'identifier',
                    'member_expression',
                ]:
                    continue

                name = context.text(name_node)
                symbol_id = self._create_symbol_id(context.file_path, name)
                signature = context.text(child).split('\n')[0].strip()
                symbol_info = SymbolInfo(
                    type="function",
                    file=context.file_path,
                    line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    signature=signature
                )
                context.symbols[symbol_id] = symbol_info
                context.symbol_lookup[name] = symbol_id
                context.functions.append(name)
                handled = True

                if value_type in ['arrow_function', 'function', 'function_expression']:
                    func_context = f"{context.file_path}::{name}"
                    context.variable_scopes.append({})
                    self._traverse_node_single_pass(
                        value_node,
                        context,
                        current_function=func_context,
                        current_class=current_class
                    )
                    context.variable_scopes.pop()

            if handled:
                return

        # Handle function calls
        elif node_type == 'call_expression':
            caller = current_function or f"{context.file_path}:{node.start_point[0] + 1}"
            called_function = self._resolve_called_function(
                node, context, current_class, current_function
            )
            if caller and called_function:
                self._register_call(context, caller, called_function)
            if caller:
                self._collect_callback_arguments(node, context, caller, current_class, current_function)

        # Handle `new Foo(...)` instantiation. Treated as a call edge to
        # the class being constructed so consumers show up in refs.
        elif node_type == 'new_expression':
            caller = self._edge_caller(context, current_function, current_class, node)
            class_name = self._resolve_new_class_name(node, context)
            if caller and class_name:
                self._register_call(context, caller, class_name)

        # Handle type annotations: ``foo: SomeType`` and return types.
        # Emits an edge from the enclosing method/class to each
        # user-defined type so DI constructor injection (NestJS/Angular)
        # and parameter-type uses are visible in the ref graph.
        # FP guard: we skip ``predefined_type`` (string/number/any etc.)
        # and only collect ``type_identifier`` leaves, so primitives and
        # anonymous object types never leak into the call graph.
        elif node_type == 'type_annotation':
            caller = self._edge_caller(context, current_function, current_class, node)
            if caller:
                for type_name in self._collect_type_identifiers(node, context):
                    self._register_call(context, caller, type_name)

        # Handle import declarations
        elif node.type == 'import_statement':
            import_text = context.text(node)
            context.imports.append(import_text)

        # Handle export declarations
        elif node.type in ['export_statement', 'export_default_declaration']:
            export_text = context.text(node)
            context.exports.append(export_text)
            # Must traverse children so exported functions/classes are properly registered
            # e.g. export default function Page() { ... } contains a function_declaration child
            for child in node.children:
                self._traverse_node_single_pass(child, context, current_function=current_function,
                                               current_class=current_class)
            return

        # Continue traversing children for other node types
        for child in node.children:
            self._traverse_node_single_pass(child, context, current_function=current_function,
                                           current_class=current_class)

    def _register_call(self, context: 'TraversalContext', caller: str, called: str) -> None:
        if called in context.symbol_lookup:
            symbol_id = context.symbol_lookup[called]
            symbol_info = context.symbols[symbol_id]
            if caller not in symbol_info.called_by:
                symbol_info.called_by.append(caller)
            return

        key = (caller, called)
        if key not in context.pending_call_set:
            context.pending_call_set.add(key)
            context.pending_calls.append(key)

    def _edge_caller(
        self,
        context: 'TraversalContext',
        current_function: Optional[str],
        current_class: Optional[str],
        node,
    ) -> str:
        """Pick a caller key for auxiliary edges (new/type_annotation).

        Prefers the enclosing method, then the enclosing class as a
        pseudo-symbol, then a ``file:line`` placeholder. The refs-table
        resolver requires the caller string to match a registered
        symbol_id for a row to land in ``refs``; the class fallback
        keeps constructor-injection edges attributable when they live
        outside any method body (decorator arg lists, field initializers).
        """
        if current_function:
            return current_function
        if current_class:
            return f"{context.file_path}::{current_class}"
        return f"{context.file_path}:{node.start_point[0] + 1}"

    def _resolve_new_class_name(self, node, context: 'TraversalContext') -> Optional[str]:
        """Extract the class name from a ``new Foo(...)`` expression."""
        ctor_node = node.child_by_field_name('constructor')
        if ctor_node is None:
            for child in node.children:
                if child.type in ('identifier', 'member_expression', 'type_identifier'):
                    ctor_node = child
                    break
        if ctor_node is None:
            return None
        if ctor_node.type in ('identifier', 'type_identifier'):
            return context.text(ctor_node)
        if ctor_node.type == 'member_expression':
            property_node = ctor_node.child_by_field_name('property')
            qualifier_node = ctor_node.child_by_field_name('object')
            qualifier = self._resolve_member_qualifier(qualifier_node, context, None)
            if property_node is None:
                return None
            prop = context.text(property_node)
            return f"{qualifier}.{prop}" if qualifier else prop
        return None

    def _collect_type_identifiers(self, node, context: 'TraversalContext') -> List[str]:
        """Yield user-defined type names reachable from a ``type_annotation``.

        Walks the sub-tree once and returns every ``type_identifier``
        leaf. Descending into ``generic_type`` and ``type_arguments``
        covers ``Array<UserDto>`` / ``Promise<Foo>``. Primitive
        ``predefined_type`` nodes are never emitted.
        """
        names: List[str] = []
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type == 'type_identifier':
                name = context.text(cur)
                if name and name not in names:
                    names.append(name)
                continue
            if cur.type == 'predefined_type':
                continue
            for child in cur.children:
                stack.append(child)
        return names

    def _collect_callback_arguments(
        self,
        node,
        context: 'TraversalContext',
        caller: str,
        current_class: Optional[str],
        current_function: Optional[str]
    ) -> None:
        arguments_node = node.child_by_field_name('arguments')
        if not arguments_node:
            return

        for argument in arguments_node.children:
            if not getattr(argument, "is_named", False):
                continue
            callback_name = self._resolve_argument_reference(argument, context, current_class)
            if callback_name:
                call_site = caller
                if current_function is None:
                    call_site = f"{context.file_path}:{argument.start_point[0] + 1}"
                self._register_call(context, call_site, callback_name)

    def _resolve_argument_reference(
        self,
        node,
        context: 'TraversalContext',
        current_class: Optional[str]
    ) -> Optional[str]:
        node_type = node.type

        if node_type == 'identifier':
            return context.text(node)

        if node_type == 'member_expression':
            property_node = node.child_by_field_name('property')
            if property_node is None:
                for child in node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            if property_node is None:
                return None

            property_name = context.text(property_node)
            qualifier_node = node.child_by_field_name('object')
            qualifier = self._resolve_member_qualifier(
                qualifier_node,
                context,
                current_class
            )
            if not qualifier:
                for child in node.children:
                    if child is property_node:
                        continue
                    qualifier = self._resolve_member_qualifier(
                        child,
                        context,
                        current_class
                    )
                    if qualifier:
                        break
            if qualifier:
                return f"{qualifier}.{property_name}"
            return property_name

        return None

    def _resolve_called_function(
        self,
        node,
        context: 'TraversalContext',
        current_class: Optional[str],
        current_function: Optional[str] = None,
    ) -> Optional[str]:
        function_node = node.child_by_field_name('function')
        if function_node is None and node.children:
            function_node = node.children[0]
        if function_node is None:
            return None

        if function_node.type == 'identifier':
            return context.text(function_node)

        if function_node.type == 'member_expression':
            property_node = function_node.child_by_field_name('property')
            if property_node is None:
                for child in function_node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            if property_node is None:
                return None

            property_name = context.text(property_node)
            qualifier_node = function_node.child_by_field_name('object')
            qualifier = self._resolve_member_qualifier(
                qualifier_node,
                context,
                current_class,
                current_function,
            )
            if not qualifier:
                for child in function_node.children:
                    if child is property_node:
                        continue
                    qualifier = self._resolve_member_qualifier(
                        child,
                        context,
                        current_class,
                        current_function,
                    )
                    if qualifier:
                        break
            if qualifier:
                return f"{qualifier}.{property_name}"
            return property_name

        return None

    def _resolve_member_qualifier(
        self,
        node,
        context: 'TraversalContext',
        current_class: Optional[str],
        current_function: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve the textual (or type-resolved) qualifier of a member
        access.

        When ``current_function`` / ``current_class`` are supplied and
        the receiver is a known typed local or ``this.<prop>``, returns
        the owning **type name** rather than the raw identifier. This
        is what lets ``svc.foo()`` and ``this.usersService.findById()``
        land on ``Svc.foo`` / ``UsersService.findById`` in the refs
        table instead of an unqualified method name.

        FN guard: if type lookup fails, falls back to the literal
        receiver text so existing behaviour is preserved.
        """
        if node is None:
            return None

        node_type = node.type
        if node_type == 'this':
            return current_class

        if node_type == 'identifier':
            var_name = context.text(node)
            # Local-var typed at assignment time wins over the bare name.
            if current_function:
                typed = context.local_types.get(current_function, {}).get(var_name)
                if typed:
                    return typed
            return var_name

        if node_type == 'member_expression':
            # ``this.<prop>`` → look up declared/promoted property type.
            object_node = node.child_by_field_name('object')
            property_node = node.child_by_field_name('property')
            if property_node is None:
                for child in node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            if (
                property_node is not None
                and object_node is not None
                and object_node.type == 'this'
                and current_class
            ):
                prop_name = context.text(property_node)
                typed = context.property_types.get(current_class, {}).get(prop_name)
                if typed:
                    return typed

            if property_node is None:
                return None

            qualifier = self._resolve_member_qualifier(
                object_node,
                context,
                current_class,
                current_function,
            )
            if not qualifier:
                for child in node.children:
                    if child is property_node:
                        continue
                    qualifier = self._resolve_member_qualifier(
                        child,
                        context,
                        current_class,
                        current_function,
                    )
                    if qualifier:
                        break

            property_name = context.text(property_node)
            if qualifier:
                return f"{qualifier}.{property_name}"
            return property_name

        return None

    # ── Receiver-type capture helpers ─────────────────────────────────

    def _capture_class_property_types(
        self,
        class_node,
        context: 'TraversalContext',
        class_name: str,
    ) -> None:
        """Populate ``context.property_types[class_name]`` from the three
        common TS/NestJS/Angular DI surfaces:

            * ``private svc: Svc``            (public_field_definition)
            * ``constructor(private svc: Svc)`` (promoted required_parameter)
            * ``constructor(svc: Svc) { this.svc = svc; }``
              (classic constructor-body assignment)

        Precedence when the same property is declared by multiple
        surfaces: field declaration wins, then promoted parameter, then
        body assignment. The first writer to ``props[prop]`` holds; later
        writers short-circuit. This keeps the stronger/explicit signal
        authoritative and prevents body-assignment inference from
        overwriting a direct type annotation.

        FP guard: body-assignment inference only resolves RHS expressions
        we can statically prove: an ``identifier`` that names a typed
        constructor parameter, or a ``new Foo()`` expression. Anything
        else (method calls, conditionals, spreads) is silently ignored,
        so opaque initializers never introduce phantom property types.
        """
        body = class_node.child_by_field_name('body')
        if body is None:
            for child in class_node.children:
                if child.type == 'class_body':
                    body = child
                    break
        if body is None:
            return

        props = context.property_types.setdefault(class_name, {})
        for member in body.children:
            if member.type == 'public_field_definition':
                prop_name: Optional[str] = None
                type_name: Optional[str] = None
                for sub in member.children:
                    if sub.type == 'property_identifier' and prop_name is None:
                        prop_name = context.text(sub)
                    elif sub.type == 'type_annotation' and type_name is None:
                        type_name = self._extract_first_type_identifier(sub, context)
                if prop_name and type_name and prop_name not in props:
                    props[prop_name] = type_name
                continue

            if member.type != 'method_definition':
                continue

            is_ctor = False
            params_node = None
            block_node = None
            for sub in member.children:
                if sub.type == 'property_identifier':
                    if context.text(sub) == 'constructor':
                        is_ctor = True
                elif sub.type == 'formal_parameters':
                    params_node = sub
                elif sub.type == 'statement_block':
                    block_node = sub
            if not is_ctor or params_node is None:
                continue

            # Build param_name -> type map for every typed parameter
            # (including non-promoted ones). Promoted params are also
            # written into ``props`` immediately so classic and promoted
            # styles can coexist on the same constructor signature.
            ctor_param_types: Dict[str, str] = {}
            for param in params_node.children:
                if param.type != 'required_parameter':
                    continue
                has_visibility = False
                p_name: Optional[str] = None
                p_type: Optional[str] = None
                for sub in param.children:
                    if sub.type == 'accessibility_modifier':
                        has_visibility = True
                    elif sub.type == 'identifier' and p_name is None:
                        p_name = context.text(sub)
                    elif sub.type == 'type_annotation' and p_type is None:
                        p_type = self._extract_first_type_identifier(sub, context)
                if p_name and p_type:
                    ctor_param_types[p_name] = p_type
                    if has_visibility and p_name not in props:
                        props[p_name] = p_type

            if block_node is not None:
                self._capture_constructor_body_assignments(
                    block_node, context, props, ctor_param_types
                )

    def _capture_constructor_body_assignments(
        self,
        block_node,
        context: 'TraversalContext',
        props: Dict[str, str],
        ctor_param_types: Dict[str, str],
    ) -> None:
        """Walk a constructor ``statement_block`` and register
        ``this.<prop> = <rhs>`` assignments whose RHS we can statically
        type-resolve. Called only once, after the constructor's formal
        parameters have been mapped to types.

        Resolves two narrow shapes (by design, to keep FP risk low):

            * ``this.x = paramName``   → use ``ctor_param_types`` lookup.
            * ``this.x = new Foo(...)`` → reuse ``_resolve_new_class_name``.

        Everything else (method calls, ternaries, spreads, ``null``
        sentinels) is a no-op so unresolved assignments never produce a
        phantom property type.
        """
        for stmt in block_node.children:
            if stmt.type != 'expression_statement':
                continue
            assign_node = None
            for child in stmt.children:
                if child.type == 'assignment_expression':
                    assign_node = child
                    break
            if assign_node is None:
                continue

            lhs = assign_node.child_by_field_name('left')
            rhs = assign_node.child_by_field_name('right')
            if lhs is None or rhs is None:
                named = [c for c in assign_node.children if c.is_named]
                if len(named) < 2:
                    continue
                lhs, rhs = named[0], named[-1]

            if lhs.type != 'member_expression':
                continue
            lhs_obj = lhs.child_by_field_name('object')
            lhs_prop = lhs.child_by_field_name('property')
            if lhs_prop is None:
                for child in lhs.children:
                    if child.type == 'property_identifier':
                        lhs_prop = child
                        break
            if lhs_obj is None or lhs_obj.type != 'this' or lhs_prop is None:
                continue
            prop_name = context.text(lhs_prop)
            # Stronger signals (field decl / promoted param) already won.
            if prop_name in props:
                continue

            type_name: Optional[str] = None
            if rhs.type == 'identifier':
                ident = context.text(rhs)
                type_name = ctor_param_types.get(ident)
            elif rhs.type == 'new_expression':
                type_name = self._resolve_new_class_name(rhs, context)
            # Everything else stays unresolved by design (FP guard).

            if type_name:
                props[prop_name] = type_name.split('.')[-1]

    def _capture_local_type(
        self,
        declarator_node,
        context: 'TraversalContext',
        func_key: str,
    ) -> None:
        """Record ``const x = new Foo()`` or ``const x: Foo = ...``
        into ``context.local_types[func_key]`` so a later ``x.bar()``
        resolves to ``Foo.bar`` in the refs table.

        FP guard: we intentionally do **not** infer types from arbitrary
        call-expression initializers (e.g. ``const user = repo.find()``)
        to avoid cross-wiring unrelated classes that happen to share a
        method name.
        """
        name_node = declarator_node.child_by_field_name('name')
        value_node = declarator_node.child_by_field_name('value')
        if name_node is None or name_node.type != 'identifier':
            return
        var_name = context.text(name_node)
        type_name: Optional[str] = None

        type_annot = declarator_node.child_by_field_name('type')
        if type_annot is None:
            for child in declarator_node.children:
                if child.type == 'type_annotation':
                    type_annot = child
                    break
        if type_annot is not None:
            type_name = self._extract_first_type_identifier(type_annot, context)

        if type_name is None and value_node is not None and value_node.type == 'new_expression':
            type_name = self._resolve_new_class_name(value_node, context)

        if type_name:
            # Strip any dotted qualifier (e.g. ``ns.Foo`` -> ``Foo``)
            # so the lookup key matches the short_name the refs
            # resolver stores.
            type_name = type_name.split('.')[-1]
            context.local_types.setdefault(func_key, {})[var_name] = type_name

    def _extract_first_type_identifier(
        self,
        type_annotation_node,
        context: 'TraversalContext',
    ) -> Optional[str]:
        """Return the first user-defined ``type_identifier`` inside a
        ``type_annotation`` subtree.

        Walks children in source order; skips ``predefined_type`` so
        primitives never leak. For union/generic types (``Foo | null``,
        ``Array<Foo>``) this returns the leftmost type_identifier which
        maps to the practical "receiver type" in DI-heavy codebases.
        """
        queue = [type_annotation_node]
        while queue:
            cur = queue.pop(0)
            if cur.type == 'type_identifier':
                return context.text(cur)
            if cur.type == 'predefined_type':
                continue
            for child in cur.children:
                queue.append(child)
        return None

    # Helpers below take ``content_bytes`` (the exact UTF-8 buffer
    # parsed by tree-sitter) rather than the ``str`` form so that
    # non-ASCII characters in the source cannot drift the slice. See
    # ``TraversalContext.text`` for the correctness rationale.

    def _get_function_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract function name from tree-sitter node."""
        for child in node.children:
            if child.type == 'identifier':
                return content_bytes[child.start_byte:child.end_byte].decode(
                    'utf-8', errors='replace',
                )
        return None

    def _get_class_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract class name from tree-sitter node.

        FN guard: tree-sitter-typescript labels class names as
        ``type_identifier`` (not ``identifier`` like plain JS). Missing
        the ``type_identifier`` branch caused every class in NestJS-style
        code to drop out of the index along with all its methods.
        """
        for child in node.children:
            if child.type in ('type_identifier', 'identifier'):
                return content_bytes[child.start_byte:child.end_byte].decode(
                    'utf-8', errors='replace',
                )
        return None

    def _get_interface_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract interface name from tree-sitter node."""
        for child in node.children:
            if child.type == 'type_identifier':
                return content_bytes[child.start_byte:child.end_byte].decode(
                    'utf-8', errors='replace',
                )
        return None

    def _get_method_name(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract method name from tree-sitter node."""
        for child in node.children:
            if child.type == 'property_identifier':
                return content_bytes[child.start_byte:child.end_byte].decode(
                    'utf-8', errors='replace',
                )
        return None

    def _get_ts_function_signature(self, node, content_bytes: bytes) -> str:
        """Extract TypeScript function signature."""
        text = content_bytes[node.start_byte:node.end_byte].decode(
            'utf-8', errors='replace',
        )
        return text.split('\n')[0].strip()


class TraversalContext:
    """Context object to pass state during single-pass traversal.

    ``content`` is the original string (kept for line counting and any
    future str-level needs). ``content_bytes`` is the UTF-8 encoding
    fed to tree-sitter; all ``start_byte``/``end_byte`` offsets returned
    by the parser are offsets into this buffer. Call sites MUST use
    :meth:`text` to extract node text so multi-byte characters do not
    corrupt the slice boundary.
    """

    def __init__(
        self,
        content: str,
        content_bytes: bytes,
        file_path: str,
        symbols: Dict,
        functions: List,
        classes: List,
        imports: List,
        exports: List,
        symbol_lookup: Dict,
        pending_calls: List[Tuple[str, str]],
        pending_call_set: Set[Tuple[str, str]],
        variable_scopes: List[Dict[str, str]],
    ):
        self.content = content
        self.content_bytes = content_bytes
        self.file_path = file_path
        self.symbols = symbols
        self.functions = functions
        self.classes = classes
        self.imports = imports
        self.exports = exports
        self.symbol_lookup = symbol_lookup
        self.pending_calls = pending_calls
        self.pending_call_set = pending_call_set
        self.variable_scopes = variable_scopes
        # Receiver-type resolution tables (see module docstring):
        #   property_types[ClassName] = {prop_name: TypeName}
        #     populated from field declarations and promoted ctor params.
        #   local_types[func_key] = {var_name: TypeName}
        #     populated from ``const x = new T()`` / explicit annotations.
        self.property_types: Dict[str, Dict[str, str]] = {}
        self.local_types: Dict[str, Dict[str, str]] = {}

    def text(self, node) -> str:
        """Return the text covered by ``node`` as a Python ``str``.

        Slices :attr:`content_bytes` using tree-sitter's byte offsets
        and decodes as UTF-8. Using ``content[start_byte:end_byte]`` on
        the ``str`` form is a correctness bug for any file containing
        non-ASCII characters: tree-sitter indexes bytes, not Unicode
        code points, so the slice drifts by the cumulative multi-byte
        width of every character before ``start_byte``. On files with
        even a single em-dash in a comment this silently mangles symbol
        names, signatures and qualifiers.
        """
        return self.content_bytes[node.start_byte:node.end_byte].decode(
            'utf-8', errors='replace',
        )
