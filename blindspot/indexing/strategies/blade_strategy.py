"""
Laravel Blade template parsing strategy.
Extracts sections, components, slots, Alpine.js data, and Blade directives.
"""

import re
import logging
from typing import Dict, List, Tuple, Optional
from .base_strategy import ParsingStrategy
from ..models import SymbolInfo, FileInfo

logger = logging.getLogger(__name__)


class BladeParsingStrategy(ParsingStrategy):
    """Laravel Blade template parsing strategy using regex patterns."""

    # Blade directive patterns
    SECTION_PATTERN = re.compile(r"@section\(['\"]([^'\"]+)['\"]\)")
    YIELD_PATTERN = re.compile(r"@yield\(['\"]([^'\"]+)['\"]\)")
    EXTENDS_PATTERN = re.compile(r"@extends\(['\"]([^'\"]+)['\"]\)")
    INCLUDE_PATTERN = re.compile(r"@(?:include(?:When|Unless|First)?|each)\(['\"]([^'\"]+)['\"]\)")
    COMPONENT_PATTERN = re.compile(r"<x-([a-zA-Z0-9._-]+)")
    PUSH_PATTERN = re.compile(r"@push\(['\"]([^'\"]+)['\"]\)")
    STACK_PATTERN = re.compile(r"@stack\(['\"]([^'\"]+)['\"]\)")
    LIVEWIRE_PATTERN = re.compile(r"@livewire\(['\"]([^'\"]+)['\"]\)")
    SLOT_PATTERN = re.compile(r"<x-slot\s+name=['\"]([^'\"]+)['\"]\s*/?>|<x-slot:([a-zA-Z0-9_-]+)")

    # Alpine.js patterns
    ALPINE_DATA_PATTERN = re.compile(r'x-data="(\{[^"]*\})"')  # simple fallback
    ALPINE_COMPONENT_PATTERN = re.compile(r"""x-data=["']([a-zA-Z_]\w*)\(""")
    ALPINE_GLOBAL_DATA_PATTERN = re.compile(r"Alpine\.data\(['\"](\w+)['\"]")
    ALPINE_STORE_PATTERN = re.compile(r"Alpine\.store\(['\"](\w+)['\"]")

    # Alpine.js directive patterns
    ALPINE_EVENT_PATTERN = re.compile(r'(?:x-on:|@)([a-zA-Z][a-zA-Z0-9._-]*)(?:\.|\s*=)')
    ALPINE_MODEL_PATTERN = re.compile(r'x-model(?:\.[\w.]+)?="([^"]+)"')
    ALPINE_REF_PATTERN = re.compile(r'x-ref="([^"]+)"')
    ALPINE_REFS_USAGE_PATTERN = re.compile(r'\$refs\.(\w+)')
    ALPINE_STORE_USAGE_PATTERN = re.compile(r'\$store\.(\w+)')
    ALPINE_TELEPORT_PATTERN = re.compile(r'x-teleport="([^"]+)"')

    # Blade component patterns
    PROPS_PATTERN = re.compile(r"@props\(\[((?:[^\[\]]*|\[[^\]]*\])*)\]\)", re.DOTALL)
    SLOT_USAGE_PATTERN = re.compile(r"\{\{\s*\$slot\s*\}\}")
    DISPATCH_PATTERN = re.compile(r"\$dispatch\(['\"]([^'\"]+)['\"]")

    # Route reference pattern
    ROUTE_PATTERN = re.compile(r"route\(['\"]([^'\"]+)['\"]\)")

    @staticmethod
    def _extract_xdata_objects(content: str) -> List[dict]:
        """
        Extract x-data inline objects using brace counting to handle
        multi-line, nested objects, and methods.
        Returns list of dicts with 'start', 'end', 'line', 'body'.
        """
        results = []
        # Find x-data=" or x-data=' followed by {
        pattern = re.compile(r"""x-data\s*=\s*(["'])""")
        for m in pattern.finditer(content):
            quote = m.group(1)
            start_pos = m.end()  # position after the opening quote
            # Check if next non-space char is {
            rest = content[start_pos:]
            stripped = rest.lstrip()
            if not stripped.startswith('{'):
                continue  # named component like x-data="componentName()", skip
            brace_start = start_pos + (len(rest) - len(stripped))
            # Count braces
            depth = 0
            i = brace_start
            in_string = None
            escape_next = False
            while i < len(content):
                ch = content[i]
                if escape_next:
                    escape_next = False
                    i += 1
                    continue
                if ch == '\\':
                    escape_next = True
                    i += 1
                    continue
                if in_string:
                    if ch == in_string:
                        in_string = None
                elif ch in ("'", '"', '`'):
                    # Don't enter string mode for the outer quote delimiter
                    # if we encounter the outer quote AND depth==0, that's end
                    if ch == quote and depth == 0:
                        break
                    in_string = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        body = content[brace_start:i + 1]
                        line = content[:m.start()].count('\n') + 1
                        results.append({
                            'start': m.start(),
                            'end': i + 1,
                            'line': line,
                            'body': body,
                        })
                        break
                i += 1
        return results

    def get_language_name(self) -> str:
        return "blade"

    def get_supported_extensions(self) -> List[str]:
        return []  # Registered manually for .blade.php

    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """Parse Blade template and extract structural information."""
        symbols = {}
        sections = []
        components = []
        imports = []  # extends, includes
        yields = []
        alpine_components = []
        alpine_inline_data = []
        alpine_stores = []
        alpine_global_components = []
        alpine_events = []
        alpine_refs = []
        alpine_models = []
        alpine_teleports = []
        alpine_store_usages = []
        routes = []
        props = []
        dispatched_events = []
        stacks = []
        has_slot = False

        lines = content.split('\n')

        # Extract parent layout
        for match in self.EXTENDS_PATTERN.finditer(content):
            imports.append(f"extends:{match.group(1)}")

        # Extract includes
        for match in self.INCLUDE_PATTERN.finditer(content):
            imports.append(f"include:{match.group(1)}")

        # Extract sections
        for match in self.SECTION_PATTERN.finditer(content):
            name = match.group(1)
            line = content[:match.start()].count('\n') + 1
            sections.append(name)
            symbol_id = self._create_symbol_id(file_path, f"section:{name}")
            symbols[symbol_id] = SymbolInfo(
                type="section",
                file=file_path,
                line=line,
                signature=f"@section('{name}')",
            )

        # Extract yields
        for match in self.YIELD_PATTERN.finditer(content):
            name = match.group(1)
            yields.append(name)

        # Extract Blade components (<x-...>) with attributes
        component_attributes = {}
        comp_tag_re = re.compile(r'<x-([\w._-]+)([^>]*?)/?>', re.DOTALL)
        attr_re = re.compile(r'(:?)([\w._-]+)\s*=\s*["\']')
        for match in comp_tag_re.finditer(content):
            name = match.group(1)
            attrs_str = match.group(2)
            line = content[:match.start()].count('\n') + 1
            if name not in components:
                components.append(name)
            symbol_id = self._create_symbol_id(file_path, f"component:{name}")
            if symbol_id not in symbols:
                symbols[symbol_id] = SymbolInfo(
                    type="component",
                    file=file_path,
                    line=line,
                    signature=f"<x-{name}>",
                )
            # Extract attributes
            if attrs_str.strip():
                if name not in component_attributes:
                    component_attributes[name] = []
                existing_names = {a["name"] for a in component_attributes[name]}
                for am in attr_re.finditer(attrs_str):
                    bound = am.group(1) == ':'
                    attr_name = am.group(2)
                    if attr_name not in existing_names:
                        component_attributes[name].append({"name": attr_name, "bound": bound})
                        existing_names.add(attr_name)

        # Extract push/stack
        for match in self.PUSH_PATTERN.finditer(content):
            imports.append(f"push:{match.group(1)}")

        # Extract Alpine.js components
        for match in self.ALPINE_COMPONENT_PATTERN.finditer(content):
            name = match.group(1)
            line = content[:match.start()].count('\n') + 1
            alpine_components.append(name)
            symbol_id = self._create_symbol_id(file_path, f"alpine:{name}")
            symbols[symbol_id] = SymbolInfo(
                type="alpine_component",
                file=file_path,
                line=line,
                signature=f'x-data="{name}()"',
            )

        # Extract Alpine.js inline x-data objects using brace-counting (multi-line safe)
        for xdata in self._extract_xdata_objects(content):
            obj_str = xdata['body']
            line = xdata['line']
            # Extract top-level property/method names from the object
            # Match "name:" at the start or after comma/newline (top-level keys)
            prop_names = re.findall(r'(?:^|[,\n])\s*(\w+)\s*(?::|(?:\([^)]*\)\s*\{))', obj_str)
            label = ', '.join(prop_names[:5])
            if len(prop_names) > 5:
                label += ', ...'
            alpine_inline_data.append({"line": line, "properties": prop_names, "label": label})
            symbol_id = self._create_symbol_id(file_path, f"alpine-inline:{line}")
            symbols[symbol_id] = SymbolInfo(
                type="alpine_inline",
                file=file_path,
                line=line,
                signature=f'x-data="{{ {label} }}"',
            )

        # Extract Alpine.data() global component definitions
        for match in self.ALPINE_GLOBAL_DATA_PATTERN.finditer(content):
            name = match.group(1)
            line = content[:match.start()].count('\n') + 1
            alpine_global_components.append(name)
            symbol_id = self._create_symbol_id(file_path, f"alpine-global:{name}")
            symbols[symbol_id] = SymbolInfo(
                type="alpine_global_component",
                file=file_path,
                line=line,
                signature=f"Alpine.data('{name}')",
            )

        # Extract Alpine.store() definitions
        for match in self.ALPINE_STORE_PATTERN.finditer(content):
            name = match.group(1)
            line = content[:match.start()].count('\n') + 1
            alpine_stores.append(name)
            symbol_id = self._create_symbol_id(file_path, f"alpine-store:{name}")
            symbols[symbol_id] = SymbolInfo(
                type="alpine_store",
                file=file_path,
                line=line,
                signature=f"Alpine.store('{name}')",
            )

        # Extract @props (component interface)
        for match in self.PROPS_PATTERN.finditer(content):
            props_str = match.group(1)
            line = content[:match.start()].count('\n') + 1
            # Parse prop names: 'key' => value (associative) or 'name' (indexed)
            # Associative: extract key before =>
            assoc_props = re.findall(r"['\"](\w+)['\"]\s*=>", props_str)
            # Remove associative parts to find indexed (standalone) props
            remaining = re.sub(r"['\"](\w+)['\"]\s*=>[^,\n]*", "", props_str)
            indexed_props = re.findall(r"['\"](\w+)['\"]", remaining)
            prop_names = indexed_props + assoc_props
            props = prop_names
            symbol_id = self._create_symbol_id(file_path, "props")
            symbols[symbol_id] = SymbolInfo(
                type="props",
                file=file_path,
                line=line,
                signature=f"@props([{', '.join(prop_names)}])",
            )

        # Extract @stack declarations
        for match in self.STACK_PATTERN.finditer(content):
            name = match.group(1)
            stacks.append(name)

        # Extract $dispatch events
        for match in self.DISPATCH_PATTERN.finditer(content):
            event = match.group(1)
            if event not in dispatched_events:
                dispatched_events.append(event)

        # Extract Alpine event handlers (x-on: and @click/@submit etc)
        for match in self.ALPINE_EVENT_PATTERN.finditer(content):
            event = match.group(1)
            if event not in alpine_events:
                alpine_events.append(event)

        # Extract x-model bindings
        for match in self.ALPINE_MODEL_PATTERN.finditer(content):
            model = match.group(1)
            if model not in alpine_models:
                alpine_models.append(model)

        # Extract x-ref definitions and $refs usages
        for match in self.ALPINE_REF_PATTERN.finditer(content):
            ref = match.group(1)
            if ref not in alpine_refs:
                alpine_refs.append(ref)
        for match in self.ALPINE_REFS_USAGE_PATTERN.finditer(content):
            ref = match.group(1)
            if ref not in alpine_refs:
                alpine_refs.append(ref)

        # Extract $store usages
        for match in self.ALPINE_STORE_USAGE_PATTERN.finditer(content):
            store = match.group(1)
            if store not in alpine_store_usages:
                alpine_store_usages.append(store)

        # Extract x-teleport targets
        for match in self.ALPINE_TELEPORT_PATTERN.finditer(content):
            target = match.group(1)
            if target not in alpine_teleports:
                alpine_teleports.append(target)

        # Check for {{ $slot }} usage
        if self.SLOT_USAGE_PATTERN.search(content):
            has_slot = True

        # Extract route references
        for match in self.ROUTE_PATTERN.finditer(content):
            route_name = match.group(1)
            if route_name not in routes:
                routes.append(route_name)

        # Extract slots
        for match in self.SLOT_PATTERN.finditer(content):
            name = match.group(1) or match.group(2)
            if name:
                line = content[:match.start()].count('\n') + 1
                symbol_id = self._create_symbol_id(file_path, f"slot:{name}")
                symbols[symbol_id] = SymbolInfo(
                    type="slot",
                    file=file_path,
                    line=line,
                    signature=f"<x-slot:{name}>",
                )

        file_info = FileInfo(
            language=self.get_language_name(),
            line_count=len(lines),
            symbols={
                "sections": sections,
                "components": components,
                "component_attributes": component_attributes,
                "yields": yields,
                "alpine_components": alpine_components,
                "alpine_inline_data": alpine_inline_data,
                "alpine_global_components": alpine_global_components,
                "alpine_stores": alpine_stores,
                "alpine_events": alpine_events,
                "alpine_refs": alpine_refs,
                "alpine_models": alpine_models,
                "alpine_teleports": alpine_teleports,
                "alpine_store_usages": alpine_store_usages,
                "routes": routes,
                "props": props,
                "dispatched_events": dispatched_events,
                "stacks": stacks,
                "has_slot": has_slot,
            },
            imports=imports,
        )

        return symbols, file_info
