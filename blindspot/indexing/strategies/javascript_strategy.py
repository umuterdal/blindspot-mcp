"""
JavaScript parsing strategy using tree-sitter.
"""

import logging
from typing import Dict, List, Tuple, Optional, Set

import tree_sitter
from tree_sitter_javascript import language

from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)


class JavaScriptParsingStrategy(ParsingStrategy):
    """JavaScript-specific parsing strategy using tree-sitter."""

    def __init__(self):
        self.js_language = tree_sitter.Language(language())

    def get_language_name(self) -> str:
        return "javascript"

    def get_supported_extensions(self) -> List[str]:
        return ['.js', '.jsx', '.mjs', '.cjs']

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse JavaScript file using tree-sitter."""
        symbols: Dict[str, SymbolInfo] = {}
        functions: List[str] = []
        classes: List[str] = []
        imports: List[str] = []
        exports: List[str] = []
        symbol_lookup: Dict[str, str] = {}
        pending_calls: List[Tuple[str, str]] = []
        pending_call_set: Set[Tuple[str, str]] = set()
        variable_scopes: List[Dict[str, str]] = [{}]
        # class_name -> {prop_name -> type_name}. Populated pre-traversal
        # from each class's field definitions and constructor body so
        # ``this.<prop>.foo()`` calls inside methods resolve to the
        # owning class's method (classic Node.js DI). Scoped per
        # ``parse_file`` call so concurrent threads do not collide.
        instance_fields: Dict[str, Dict[str, str]] = {}

        parser = tree_sitter.Parser(self.js_language)
        # Encode once and share with tree-sitter. All start_byte/end_byte
        # offsets emitted by the parser index into this exact UTF-8
        # buffer; slicing the decoded ``str`` with those offsets
        # silently mangles symbol names and signatures whenever the file
        # contains non-ASCII bytes. Helpers below consume ``content_bytes``
        # directly and decode only when returning text to callers.
        content_bytes = content.encode('utf-8')
        tree = parser.parse(content_bytes)
        self._traverse_js_node(
            tree.root_node,
            content_bytes,
            file_path,
            symbols,
            functions,
            classes,
            imports,
            exports,
            symbol_lookup,
            pending_calls,
            pending_call_set,
            variable_scopes,
            instance_fields,
        )

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(content.splitlines()),
            symbols={"functions": functions, "classes": classes},
            imports=imports,
            exports=exports
        )

        if pending_calls:
            file_info.pending_calls = pending_calls

        return symbols, file_info

    def _traverse_js_node(
        self,
        node,
        content: bytes,
        file_path: str,
        symbols: Dict[str, SymbolInfo],
        functions: List[str],
        classes: List[str],
        imports: List[str],
        exports: List[str],
        symbol_lookup: Dict[str, str],
        pending_calls: List[Tuple[str, str]],
        pending_call_set: Set[Tuple[str, str]],
        variable_scopes: List[Dict[str, str]],
        instance_fields: Dict[str, Dict[str, str]],
        current_function: Optional[str] = None,
        current_class: Optional[str] = None,
    ):
        """Traverse JavaScript AST node and collect symbols and relationships."""
        node_type = node.type

        if node_type == 'function_declaration':
            name = self._get_function_name(node, content)
            if name:
                symbol_id = self._create_symbol_id(file_path, name)
                signature = self._get_js_function_signature(node, content)
                symbols[symbol_id] = SymbolInfo(
                    type="function",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=signature
                )
                symbol_lookup[name] = symbol_id
                functions.append(name)
                function_id = f"{file_path}::{name}"
                variable_scopes.append({})
                for child in node.children:
                    self._traverse_js_node(
                        child,
                        content,
                        file_path,
                        symbols,
                        functions,
                        classes,
                        imports,
                        exports,
                        symbol_lookup,
                        pending_calls,
                        pending_call_set,
                        variable_scopes,
                        instance_fields,
                        current_function=function_id,
                        current_class=current_class,
                    )
                variable_scopes.pop()
            return

        if node_type == 'class_declaration':
            name = self._get_class_name(node, content)
            if name:
                symbol_id = self._create_symbol_id(file_path, name)
                symbols[symbol_id] = SymbolInfo(
                    type="class",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1
                )
                symbol_lookup[name] = symbol_id
                classes.append(name)
                # Pre-pass: capture ``this.<prop> = new Foo()`` in the
                # constructor body and ``prop = new Foo()`` class fields
                # before walking methods. Lets subsequent method bodies
                # resolve ``this.<prop>.bar()`` to the declared class's
                # ``bar`` when registering call edges. Mirrors TS
                # strategy's property_types without the TS-only type
                # annotation surfaces (field decl / promoted param).
                self._capture_class_instance_fields(
                    node, content, name, instance_fields
                )
                for child in node.children:
                    self._traverse_js_node(
                        child,
                        content,
                        file_path,
                        symbols,
                        functions,
                        classes,
                        imports,
                        exports,
                        symbol_lookup,
                        pending_calls,
                        pending_call_set,
                        variable_scopes,
                        instance_fields,
                        current_function=current_function,
                        current_class=name,
                    )
                return

        if node_type == 'method_definition':
            method_name = self._get_method_name(node, content)
            class_name = current_class or self._find_parent_class(node, content)
            if method_name and class_name:
                full_name = f"{class_name}.{method_name}"
                symbol_id = self._create_symbol_id(file_path, full_name)
                signature = self._get_js_function_signature(node, content)
                symbols[symbol_id] = SymbolInfo(
                    type="method",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=signature
                )
                symbol_lookup[full_name] = symbol_id
                symbol_lookup[method_name] = symbol_id
                functions.append(full_name)
                function_id = f"{file_path}::{full_name}"
                variable_scopes.append({})
                for child in node.children:
                    self._traverse_js_node(
                        child,
                        content,
                        file_path,
                        symbols,
                        functions,
                        classes,
                        imports,
                        exports,
                        symbol_lookup,
                        pending_calls,
                        pending_call_set,
                        variable_scopes,
                        instance_fields,
                        current_function=function_id,
                        current_class=class_name,
                    )
                variable_scopes.pop()
            return

        if node_type in ['lexical_declaration', 'variable_declaration']:
            for child in node.children:
                if child.type != 'variable_declarator':
                    self._traverse_js_node(
                        child,
                        content,
                        file_path,
                        symbols,
                        functions,
                        classes,
                        imports,
                        exports,
                        symbol_lookup,
                        pending_calls,
                        pending_call_set,
                        variable_scopes,
                        instance_fields,
                        current_function=current_function,
                        current_class=current_class,
                    )
                    continue

                name_node = child.child_by_field_name('name')
                value_node = child.child_by_field_name('value')
                if not name_node:
                    continue

                name = self._get_node_text(name_node, content)

                if value_node and value_node.type in ['arrow_function', 'function_expression', 'function']:
                    symbol_id = self._create_symbol_id(file_path, name)
                    signature = content[child.start_byte:child.end_byte].decode('utf-8', errors='replace').split('\n')[0].strip()
                    symbols[symbol_id] = SymbolInfo(
                        type="function",
                        file=file_path,
                        line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        signature=signature
                    )
                    symbol_lookup[name] = symbol_id
                    functions.append(name)
                    function_id = f"{file_path}::{name}"
                    variable_scopes.append({})
                    self._traverse_js_node(
                        value_node,
                        content,
                        file_path,
                        symbols,
                        functions,
                        classes,
                        imports,
                        exports,
                        symbol_lookup,
                        pending_calls,
                        pending_call_set,
                        variable_scopes,
                        instance_fields,
                        current_function=function_id,
                        current_class=current_class,
                    )
                    variable_scopes.pop()
                else:
                    inferred = self._infer_expression_type(value_node, content)
                    if inferred:
                        self._set_variable_type(variable_scopes, name, inferred)
                    if value_node:
                        self._traverse_js_node(
                            value_node,
                            content,
                            file_path,
                            symbols,
                            functions,
                            classes,
                            imports,
                            exports,
                            symbol_lookup,
                            pending_calls,
                            pending_call_set,
                            variable_scopes,
                            instance_fields,
                            current_function=current_function,
                            current_class=current_class,
                        )
            return

        if node_type == 'arrow_function':
            variable_scopes.append({})
            for child in node.children:
                self._traverse_js_node(
                    child,
                    content,
                    file_path,
                    symbols,
                    functions,
                    classes,
                    imports,
                    exports,
                    symbol_lookup,
                    pending_calls,
                    pending_call_set,
                    variable_scopes,
                    instance_fields,
                    current_function=current_function,
                    current_class=current_class,
                )
            variable_scopes.pop()
            return

        if node_type == 'call_expression':
            caller = current_function or f"{file_path}:{node.start_point[0] + 1}"
            called = self._resolve_called_function(
                node,
                content,
                variable_scopes,
                current_class,
                instance_fields,
            )
            if caller and called:
                self._register_call(
                    symbols,
                    symbol_lookup,
                    pending_calls,
                    pending_call_set,
                    caller,
                    called
                )
            if caller:
                self._collect_callback_arguments(
                    node,
                    content,
                    symbols,
                    symbol_lookup,
                    pending_calls,
                    pending_call_set,
                    variable_scopes,
                    current_class,
                    caller,
                    instance_fields,
                )

        if node_type in ['import_statement', 'require_call']:
            import_text = self._get_node_text(node, content)
            imports.append(import_text)
        elif node_type in ['export_statement', 'export_clause', 'export_default_declaration']:
            exports.append(self._get_node_text(node, content))

        for child in node.children:
            self._traverse_js_node(
                child,
                content,
                file_path,
                symbols,
                functions,
                classes,
                imports,
                exports,
                symbol_lookup,
                pending_calls,
                pending_call_set,
                variable_scopes,
                instance_fields,
                current_function=current_function,
                current_class=current_class,
            )

    def _collect_callback_arguments(
        self,
        call_node,
        content: bytes,
        symbols: Dict[str, SymbolInfo],
        symbol_lookup: Dict[str, str],
        pending_calls: List[Tuple[str, str]],
        pending_call_set: Set[Tuple[str, str]],
        variable_scopes: List[Dict[str, str]],
        current_class: Optional[str],
        caller: str,
        instance_fields: Dict[str, Dict[str, str]],
    ) -> None:
        """Capture identifier callbacks passed as call expression arguments."""
        arguments_node = call_node.child_by_field_name('arguments')
        if not arguments_node:
            return

        for argument in arguments_node.children:
            if not getattr(argument, "is_named", False):
                continue
            callback_name = self._resolve_argument_reference(
                argument,
                content,
                variable_scopes,
                current_class,
                instance_fields,
            )
            if not callback_name:
                continue
            self._register_call(
                symbols,
                symbol_lookup,
                pending_calls,
                pending_call_set,
                caller,
                callback_name
            )

    def _resolve_argument_reference(
        self,
        node,
        content: bytes,
        variable_scopes: List[Dict[str, str]],
        current_class: Optional[str],
        instance_fields: Dict[str, Dict[str, str]],
    ) -> Optional[str]:
        """Resolve a potential callback reference used as an argument."""
        node_type = node.type

        if node_type == 'identifier':
            return self._get_node_text(node, content)

        if node_type == 'member_expression':
            property_node = node.child_by_field_name('property')
            if property_node is None:
                for child in node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            if property_node is None:
                return None

            property_name = self._get_node_text(property_node, content)
            qualifier_node = node.child_by_field_name('object')
            qualifier = None
            if qualifier_node is not None:
                qualifier = self._resolve_member_qualifier(
                    qualifier_node,
                    content,
                    variable_scopes,
                    current_class,
                    instance_fields,
                )
            if not qualifier:
                for child in node.children:
                    if child is property_node:
                        continue
                    qualifier = self._resolve_member_qualifier(
                        child,
                        content,
                        variable_scopes,
                        current_class,
                        instance_fields,
                    )
                    if qualifier:
                        break
            if qualifier:
                return f"{qualifier}.{property_name}"
            return property_name

        if node_type in ['call_expression', 'arrow_function', 'function', 'function_expression']:
            return None

        return None

    def _get_function_name(self, node, content: bytes) -> Optional[str]:
        """Extract function name from tree-sitter node."""
        for child in node.children:
            if child.type == 'identifier':
                return self._get_node_text(child, content)
        return None

    def _get_class_name(self, node, content: bytes) -> Optional[str]:
        """Extract class name from tree-sitter node."""
        for child in node.children:
            if child.type == 'identifier':
                return self._get_node_text(child, content)
        return None

    def _get_method_name(self, node, content: bytes) -> Optional[str]:
        """Extract method name from tree-sitter node."""
        for child in node.children:
            if child.type == 'property_identifier':
                return self._get_node_text(child, content)
        return None

    def _find_parent_class(self, node, content: bytes) -> Optional[str]:
        """Find the parent class of a method."""
        parent = node.parent
        while parent:
            if parent.type == 'class_declaration':
                return self._get_class_name(parent, content)
            parent = parent.parent
        return None

    def _get_js_function_signature(self, node, content: bytes) -> str:
        """Extract JavaScript function signature.

        ``content`` is the exact UTF-8 buffer tree-sitter parsed; slicing
        it with ``start_byte``/``end_byte`` is byte-safe, decoding only
        the sliced window keeps non-ASCII signatures intact.
        """
        return content[node.start_byte:node.end_byte].decode(
            'utf-8', errors='replace',
        ).split('\n')[0].strip()

    def _get_node_text(self, node, content: bytes) -> str:
        """Byte-safe text extraction.

        Tree-sitter emits byte offsets; slicing the decoded ``str`` with
        those offsets silently mangles identifiers once the file
        contains non-ASCII characters. Slicing the UTF-8 buffer and
        decoding only that window keeps every symbol name byte-exact.
        """
        return content[node.start_byte:node.end_byte].decode(
            'utf-8', errors='replace',
        )

    def _set_variable_type(self, variable_scopes: List[Dict[str, str]], name: str, value: str) -> None:
        if not variable_scopes:
            return
        variable_scopes[-1][name] = value

    def _lookup_variable_type(self, variable_scopes: List[Dict[str, str]], name: str) -> Optional[str]:
        for scope in reversed(variable_scopes):
            if name in scope:
                return scope[name]
        return None

    def _infer_expression_type(self, node, content: bytes) -> Optional[str]:
        """Infer the class/type from a simple expression like `new ClassName()`."""
        if node is None:
            return None

        if node.type == 'new_expression':
            constructor_node = node.child_by_field_name('constructor')
            if constructor_node is None:
                # Fallback: first identifier or member expression child
                for child in node.children:
                    if child.type in ['identifier', 'member_expression']:
                        constructor_node = child
                        break

            if constructor_node:
                if constructor_node.type == 'identifier':
                    return self._get_node_text(constructor_node, content)
                if constructor_node.type == 'member_expression':
                    property_node = constructor_node.child_by_field_name('property')
                    if property_node:
                        return self._get_node_text(property_node, content)
                    for child in reversed(constructor_node.children):
                        if child.type in ['identifier', 'property_identifier']:
                            return self._get_node_text(child, content)
        return None

    # ── Instance-field capture (JS DI) ────────────────────────────────

    def _capture_class_instance_fields(
        self,
        class_node,
        content: bytes,
        class_name: str,
        instance_fields: Dict[str, Dict[str, str]],
    ) -> None:
        """Populate ``instance_fields[class_name]`` from the two JS DI
        surfaces the resolver can statically type:

            * ``class Foo { svc = new Svc(); ... }``
              (ES2022 public class field_definition)
            * ``class Foo { constructor() { this.svc = new Svc(); } }``
              (classic constructor-body assignment)

        FP guard: RHS is restricted to ``new_expression`` whose
        constructor identifier we can read directly. Opaque shapes
        (method-returning factories, ternaries, destructured params)
        are skipped so no phantom property types leak in. Field
        declaration wins over body assignment for the same prop name.
        Mirrors TS strategy's ``_capture_class_property_types`` minus
        the TS-only type-annotation surfaces.
        """
        body = None
        for child in class_node.children:
            if child.type == 'class_body':
                body = child
                break
        if body is None:
            return

        props = instance_fields.setdefault(class_name, {})

        for member in body.children:
            if member.type == 'field_definition':
                name_node = member.child_by_field_name('name')
                value_node = member.child_by_field_name('value')
                if name_node is None:
                    for child in member.children:
                        if child.type in ('property_identifier', 'identifier'):
                            name_node = child
                            break
                if name_node is None or value_node is None:
                    continue
                prop_name = self._get_node_text(name_node, content)
                type_name = self._infer_expression_type(value_node, content)
                if prop_name and type_name and prop_name not in props:
                    props[prop_name] = type_name
                continue

            if member.type != 'method_definition':
                continue

            is_ctor = False
            block_node = None
            for sub in member.children:
                if sub.type == 'property_identifier':
                    if self._get_node_text(sub, content) == 'constructor':
                        is_ctor = True
                elif sub.type == 'statement_block':
                    block_node = sub
            if not is_ctor or block_node is None:
                continue

            self._capture_constructor_body_assignments(
                block_node, content, props
            )

    def _capture_constructor_body_assignments(
        self,
        block_node,
        content: bytes,
        props: Dict[str, str],
    ) -> None:
        """Walk a ``constructor`` ``statement_block`` and register each
        ``this.<prop> = new Foo(...)`` assignment's prop → type. Only
        ``new_expression`` RHS is resolved; ``this.x = paramName`` is
        skipped because JS has no type annotations to infer the param
        type from. Runs once per class, after field definitions, so
        field-decl types are not overwritten by body assignments.
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
            prop_name = self._get_node_text(lhs_prop, content)
            # Class field declaration already wins.
            if prop_name in props:
                continue
            if rhs.type != 'new_expression':
                continue
            type_name = self._infer_expression_type(rhs, content)
            if type_name:
                props[prop_name] = type_name

    def _resolve_called_function(
        self,
        node,
        content: bytes,
        variable_scopes: List[Dict[str, str]],
        current_class: Optional[str],
        instance_fields: Dict[str, Dict[str, str]],
    ) -> Optional[str]:
        function_node = node.child_by_field_name('function')
        if function_node is None and node.children:
            function_node = node.children[0]
        if function_node is None:
            return None

        if function_node.type == 'identifier':
            return self._get_node_text(function_node, content)

        if function_node.type == 'member_expression':
            property_node = function_node.child_by_field_name('property')
            if property_node is None:
                for child in function_node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            if property_node is None:
                return None

            property_name = self._get_node_text(property_node, content)
            object_node = function_node.child_by_field_name('object')
            qualifier = None
            if object_node is not None:
                qualifier = self._resolve_member_qualifier(
                    object_node,
                    content,
                    variable_scopes,
                    current_class,
                    instance_fields,
                )
            else:
                for child in function_node.children:
                    if child is property_node:
                        continue
                    qualifier = self._resolve_member_qualifier(
                        child,
                        content,
                        variable_scopes,
                        current_class,
                        instance_fields,
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
        content: bytes,
        variable_scopes: List[Dict[str, str]],
        current_class: Optional[str],
        instance_fields: Dict[str, Dict[str, str]],
    ) -> Optional[str]:
        node_type = node.type
        if node_type == 'this':
            return current_class

        if node_type == 'identifier':
            name = self._get_node_text(node, content)
            var_type = self._lookup_variable_type(variable_scopes, name)
            return var_type or name

        if node_type == 'member_expression':
            object_node = node.child_by_field_name('object')
            property_node = node.child_by_field_name('property')
            if property_node is None:
                for child in node.children:
                    if child.type in ['property_identifier', 'identifier']:
                        property_node = child
                        break
            # ``this.<prop>`` → instance-field type lookup. Registered by
            # ``_capture_class_instance_fields`` for any ``this.<prop> =
            # new Foo()`` in the constructor body or ``<prop> = new
            # Foo()`` class field. Lets ``this.svc.bar()`` land on
            # ``Foo.bar`` in the refs table instead of an unqualified
            # ``Foo.<prop>.bar`` that would never resolve.
            if (
                property_node is not None
                and object_node is not None
                and object_node.type == 'this'
                and current_class
            ):
                prop_name = self._get_node_text(property_node, content)
                typed = instance_fields.get(current_class, {}).get(prop_name)
                if typed:
                    return typed

            if property_node is None:
                return None

            qualifier = self._resolve_member_qualifier(
                object_node,
                content,
                variable_scopes,
                current_class,
                instance_fields,
            )
            property_name = self._get_node_text(property_node, content)
            if qualifier:
                return f"{qualifier}.{property_name}"
            return property_name

        return None

    def _register_call(
        self,
        symbols: Dict[str, SymbolInfo],
        symbol_lookup: Dict[str, str],
        pending_calls: List[Tuple[str, str]],
        pending_call_set: Set[Tuple[str, str]],
        caller: str,
        called: str
    ) -> None:
        if called in symbol_lookup:
            symbol_info = symbols[symbol_lookup[called]]
            if caller not in symbol_info.called_by:
                symbol_info.called_by.append(caller)
            return

        key = (caller, called)
        if key not in pending_call_set:
            pending_call_set.add(key)
            pending_calls.append(key)
