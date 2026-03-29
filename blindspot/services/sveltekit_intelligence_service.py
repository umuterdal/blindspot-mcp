"""
SvelteKit Intelligence Service.

Provides cross-file analysis for SvelteKit projects:
- Svelte component tree and dependency mapping
- SvelteKit file-based route parsing (+page, +layout, +server, +error)
- Svelte store discovery (writable, readable, derived)
- Load function and form action analysis
- Hooks chain resolution (handle, handleError, handleFetch)
- Component prop/slot/event extraction
- Project convention extraction
- Similar pattern discovery
- Pre-edit impact analysis
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class SvelteKitIntelligenceService(BaseService):
    """SvelteKit / Svelte code intelligence."""

    # Svelte lifecycle functions
    SVELTE_LIFECYCLE = [
        "onMount", "onDestroy", "beforeUpdate", "afterUpdate", "tick",
    ]

    # SvelteKit module exports
    SVELTEKIT_EXPORTS = [
        "load", "actions", "GET", "POST", "PUT", "PATCH", "DELETE",
        "HEAD", "OPTIONS", "fallback", "entries", "prerender", "ssr", "csr",
    ]

    # Pattern registry for get_similar_patterns
    PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
        "modal": {
            "keywords": ["modal", "dialog", "overlay", "backdrop", "popup"],
            "file_patterns": [r"modal", r"dialog", r"popup", r"overlay"],
            "code_patterns": [
                r"(?:show|open|is)(?:Modal|Dialog|Popup)",
                r"on:close|on:dismiss|handleClose",
                r"transition:fade|transition:fly|transition:slide",
                r"use:portal|use:clickOutside",
            ],
        },
        "form-action": {
            "keywords": ["form", "action", "enhance", "submit", "validation"],
            "file_patterns": [r"form", r"action", r"input", r"field"],
            "code_patterns": [
                r"use:enhance",
                r"export\s+const\s+actions",
                r"<form\s+method\s*=\s*['\"]POST['\"]",
                r"fail\s*\(",
                r"\$page\.form",
                r"applyAction|deserialize",
            ],
        },
        "auth-guard": {
            "keywords": ["auth", "login", "session", "guard", "protect"],
            "file_patterns": [r"auth", r"login", r"sign", r"session", r"guard"],
            "code_patterns": [
                r"event\.locals\.\w*(?:user|session|auth)",
                r"redirect\s*\(\s*\d+",
                r"hooks\.server",
                r"getSession|validateSession",
                r"\+layout\.server\.\w+.*load",
            ],
        },
        "toast": {
            "keywords": ["toast", "notification", "snackbar", "alert", "banner"],
            "file_patterns": [r"toast", r"notification", r"snackbar", r"alert"],
            "code_patterns": [
                r"(?:add|show|dismiss|remove)(?:Toast|Notification)",
                r"writable.*(?:toast|notification)",
                r"transition:fly|transition:fade.*toast",
                r"#each\s+\$(?:toast|notification)",
            ],
        },
        "dropdown": {
            "keywords": ["dropdown", "select", "menu", "popover", "combobox"],
            "file_patterns": [r"dropdown", r"select", r"menu", r"popover", r"combobox"],
            "code_patterns": [
                r"(?:is|show)(?:Open|Dropdown|Menu)",
                r"use:clickOutside",
                r"transition:slide|transition:fly",
                r"on:select|on:change",
            ],
        },
        "table": {
            "keywords": ["table", "datagrid", "data-table", "grid", "list"],
            "file_patterns": [r"table", r"datagrid", r"data.?table", r"grid"],
            "code_patterns": [
                r"<table\b",
                r"<thead|<tbody|<th\b|<td\b",
                r"sortBy|sortDirection|sortable",
                r"#each\s+.*(?:row|item|record)",
            ],
        },
        "pagination": {
            "keywords": ["pagination", "paginate", "pager", "page-size", "cursor"],
            "file_patterns": [r"pagination", r"pagina"],
            "code_patterns": [
                r"(?:current|total)Page|pageSize|pageCount",
                r"(?:next|prev|first|last)Page",
                r"offset|cursor|skip|take|limit",
                r"\$page\.url\.searchParams",
            ],
        },
        "search": {
            "keywords": ["search", "filter", "query", "autocomplete", "typeahead"],
            "file_patterns": [r"search", r"filter", r"autocomplete"],
            "code_patterns": [
                r"(?:search|filter|query)(?:Term|Text|Value|Input)",
                r"debounce|setTimeout.*search",
                r"on:input.*search|on:keyup.*search",
                r"goto\s*\(.*searchParams",
            ],
        },
        "layout": {
            "keywords": ["layout", "shell", "wrapper", "container", "scaffold"],
            "file_patterns": [r"layout", r"shell", r"wrapper", r"scaffold"],
            "code_patterns": [
                r"\+layout\.svelte",
                r"<slot\s*/?>|<slot\b",
                r"__layout",
                r"<nav\b|<header\b|<aside\b|<footer\b",
            ],
        },
        "error-boundary": {
            "keywords": ["error", "boundary", "fallback", "catch", "404"],
            "file_patterns": [r"error", r"not.?found", r"fallback"],
            "code_patterns": [
                r"\+error\.svelte",
                r"\$page\.error",
                r"\$page\.status",
                r"handleError",
                r"<svelte:boundary",
            ],
        },
    }

    # ── helpers ────────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    def _walk_files(
        self,
        directory: str,
        extensions: Tuple[str, ...] = (".svelte", ".ts", ".js"),
    ) -> List[str]:
        """Walk directory and return files matching extensions."""
        results: List[str] = []
        if not os.path.isdir(directory):
            return results
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.endswith(extensions):
                    results.append(os.path.join(root, fname))
        return results

    def _read_file(self, fpath: str) -> Optional[str]:
        """Read file content safely."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    # ── Svelte-specific parsing helpers ───────────────────────────────

    def _extract_script_block(self, content: str, module: bool = False) -> Optional[str]:
        """Extract <script> or <script context='module'> block from a .svelte file."""
        if module:
            pattern = re.compile(
                r"<script\s+[^>]*context\s*=\s*['\"]module['\"][^>]*>(.*?)</script>",
                re.DOTALL,
            )
        else:
            # Match <script> that does NOT have context="module"
            pattern = re.compile(
                r"<script(?:\s+lang\s*=\s*['\"]ts['\"])?\s*>(.*?)</script>",
                re.DOTALL,
            )
        m = pattern.search(content)
        return m.group(1) if m else None

    def _extract_style_block(self, content: str) -> Optional[str]:
        """Extract <style> block from a .svelte file."""
        pattern = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
        m = pattern.search(content)
        return m.group(1) if m else None

    def _extract_template_block(self, content: str) -> str:
        """Extract the template (markup) portion by removing script/style blocks."""
        cleaned = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
        cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned, flags=re.DOTALL)
        return cleaned.strip()

    def _extract_props(self, script: str) -> List[Dict[str, Any]]:
        """Extract `export let` prop declarations from a script block."""
        props: List[Dict[str, Any]] = []
        # export let name: Type = default;
        pattern = re.compile(
            r"export\s+let\s+(\w+)\s*(?::\s*([\w<>\[\]|&\s]+?))?\s*(?:=\s*([^;]+))?\s*;",
        )
        for m in pattern.finditer(script):
            prop: Dict[str, Any] = {
                "name": m.group(1),
            }
            if m.group(2):
                prop["type"] = m.group(2).strip()
            if m.group(3):
                prop["default"] = m.group(3).strip()
            props.append(prop)
        return props

    def _extract_events(self, script: str, template: str) -> List[str]:
        """Extract dispatched events from a component."""
        events: List[str] = []
        # createEventDispatcher usage
        if "createEventDispatcher" in script or "dispatch" in script:
            # dispatch('eventname', ...)
            for m in re.finditer(r"dispatch\s*\(\s*['\"](\w+)['\"]", script):
                if m.group(1) not in events:
                    events.append(m.group(1))
        # Also check template for dispatch calls inside on: handlers
        for m in re.finditer(r"dispatch\s*\(\s*['\"](\w+)['\"]", template):
            if m.group(1) not in events:
                events.append(m.group(1))
        return events

    def _extract_slots(self, template: str) -> List[Dict[str, Any]]:
        """Extract <slot> definitions from template markup."""
        slots: List[Dict[str, Any]] = []
        # Named slots: <slot name="header" />  or  <slot name="header">fallback</slot>
        for m in re.finditer(r"<slot\s+name\s*=\s*['\"](\w+)['\"]", template):
            slots.append({"name": m.group(1), "named": True})
        # Default slot: <slot /> or <slot>
        if re.search(r"<slot\s*/?>|<slot\s*>", template):
            # Check it's not a named slot already matched
            default_match = re.search(r"<slot\s*/?>|<slot\s*>(?!</slot>)", template)
            if default_match:
                slots.insert(0, {"name": "default", "named": False})
        return slots

    def _extract_reactive_statements(self, script: str) -> List[str]:
        """Extract $: reactive statements."""
        statements: List[str] = []
        for m in re.finditer(r"^\s*\$:\s*(.+)$", script, re.MULTILINE):
            statements.append(m.group(1).strip().rstrip(";"))
        return statements

    def _extract_store_subscriptions(self, content: str) -> List[str]:
        """Extract $store auto-subscriptions from component content."""
        stores: Set[str] = set()
        # $storeName usage in template or script
        for m in re.finditer(r"\$(\w+)", content):
            name = m.group(1)
            # Filter out known Svelte built-ins that aren't stores
            if name not in (":", "app", "env") and not name.startswith("_"):
                stores.add(name)
        # Remove common false positives
        stores.discard("page")  # will add back if imported from $app
        stores.discard("env")
        # Check if $page is from $app/stores
        if re.search(r"\$page\b", content):
            stores.add("page")
        return sorted(stores)

    def _extract_context_usage(self, script: str) -> Dict[str, List[str]]:
        """Extract getContext/setContext calls."""
        result: Dict[str, List[str]] = {"provides": [], "consumes": []}
        for m in re.finditer(r"setContext\s*\(\s*['\"](\w+)['\"]", script):
            result["provides"].append(m.group(1))
        for m in re.finditer(r"getContext\s*\(\s*['\"](\w+)['\"]", script):
            result["consumes"].append(m.group(1))
        return result

    def _extract_imports(self, script: str) -> List[Dict[str, Any]]:
        """Extract import statements from a script block."""
        imports: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"""import\s+(?:"""
            r"""(?:(\w+)\s*,?\s*)?"""  # default import
            r"""(?:\{([^}]*)\}\s*,?\s*)?"""  # named imports
            r"""(?:\*\s+as\s+(\w+)\s*)?"""  # namespace import
            r""")?\s*from\s*['"]([@\w$./-]+)['"]""",
            re.MULTILINE,
        )
        for m in pattern.finditer(script):
            default_import = m.group(1)
            named_raw = m.group(2)
            namespace = m.group(3)
            source = m.group(4)

            named: List[str] = []
            if named_raw:
                for n in re.findall(r"(\w+)(?:\s+as\s+\w+)?", named_raw):
                    named.append(n)

            imports.append({
                "source": source,
                "default": default_import,
                "named": named,
                "namespace": namespace,
            })

        # Dynamic imports
        dynamic_pattern = re.compile(r"""(?:import)\s*\(\s*['"]([@\w$./-]+)['"]\s*\)""")
        for m in dynamic_pattern.finditer(script):
            imports.append({
                "source": m.group(1),
                "default": None,
                "named": [],
                "namespace": None,
                "dynamic": True,
            })

        return imports

    def _extract_child_components(self, template: str) -> List[str]:
        """Extract child component usages from template markup (PascalCase tags)."""
        children: Set[str] = set()
        # Match <ComponentName or <ComponentName.Sub
        for m in re.finditer(r"<([A-Z]\w+)(?:\.\w+)?\b", template):
            children.add(m.group(1))
        return sorted(children)

    def _extract_route_from_path(self, file_path: str, routes_dir: str) -> str:
        """Convert a file system path under src/routes to a SvelteKit route string."""
        rel = os.path.relpath(os.path.dirname(file_path), routes_dir)
        if rel == ".":
            return "/"
        # Convert path segments, handling groups and params
        parts = rel.replace(os.sep, "/").split("/")
        route_parts: List[str] = []
        for part in parts:
            if part.startswith("(") and part.endswith(")"):
                # Route group — not part of URL
                continue
            elif part.startswith("[") and part.endswith("]"):
                # Dynamic param
                route_parts.append(part)
            else:
                route_parts.append(part)
        route = "/" + "/".join(route_parts) if route_parts else "/"
        return route

    def _find_routes_dir(self, base: str) -> Optional[str]:
        """Find the src/routes directory."""
        routes_dir = os.path.join(base, "src", "routes")
        if os.path.isdir(routes_dir):
            return routes_dir
        return None

    def _find_lib_dir(self, base: str) -> Optional[str]:
        """Find the src/lib directory."""
        lib_dir = os.path.join(base, "src", "lib")
        if os.path.isdir(lib_dir):
            return lib_dir
        return None

    # ── 1. get_component_tree ─────────────────────────────────────────

    def get_component_tree(self, component_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse .svelte files: <script> exports (export let prop), slots, events
        (dispatch/createEventDispatcher), $: reactive statements, stores ($store),
        context (getContext/setContext). Extract component hierarchy.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Gather component directories
        component_dirs: List[str] = []
        lib_components = os.path.join(base, "src", "lib", "components")
        lib_dir = os.path.join(base, "src", "lib")
        routes_dir = os.path.join(base, "src", "routes")

        for d in [lib_components, lib_dir, routes_dir]:
            if os.path.isdir(d):
                component_dirs.append(d)

        if not component_dirs:
            return {"status": "error", "message": "No component directories found (checked src/lib/components, src/lib, src/routes)"}

        tree: Dict[str, Any] = {}

        for cdir in component_dirs:
            for fpath in self._walk_files(cdir, (".svelte",)):
                content = self._read_file(fpath)
                if not content:
                    continue

                fname = os.path.basename(fpath)
                name = fname.replace(".svelte", "")

                # Skip SvelteKit route files for component tree (handle them in route_map)
                if fname.startswith("+"):
                    continue

                if component_name and name.lower() != component_name.lower():
                    continue

                rel_path = os.path.relpath(fpath, base)
                script = self._extract_script_block(content) or ""
                module_script = self._extract_script_block(content, module=True) or ""
                template = self._extract_template_block(content)
                style_block = self._extract_style_block(content)

                props = self._extract_props(script)
                events = self._extract_events(script, template)
                slots = self._extract_slots(template)
                reactive = self._extract_reactive_statements(script)
                store_subs = self._extract_store_subscriptions(content)
                context = self._extract_context_usage(script + module_script)
                imports = self._extract_imports(script + module_script)
                children = self._extract_child_components(template)

                # Detect lifecycle hooks
                lifecycle: List[str] = []
                for hook in self.SVELTE_LIFECYCLE:
                    if re.search(rf"\b{hook}\s*\(", script):
                        lifecycle.append(hook)

                # Detect style type
                style_info: Dict[str, Any] = {}
                if style_block is not None:
                    style_info["scoped"] = True
                    if re.search(r":global\s*\(", style_block):
                        style_info["has_global"] = True
                    if re.search(r"@apply\b", style_block):
                        style_info["uses_tailwind_apply"] = True
                # Check for class: directive (Tailwind)
                if re.search(r'class\s*=\s*["\']', template):
                    style_info["uses_classes"] = True

                # Detect TypeScript
                uses_ts = bool(re.search(r'<script\s+lang\s*=\s*["\']ts["\']', content))

                tree[name] = {
                    "file": rel_path,
                    "props": props,
                    "events": events,
                    "slots": slots,
                    "reactive_statements": reactive[:10],
                    "store_subscriptions": store_subs,
                    "context": context,
                    "children_components": children,
                    "lifecycle_hooks": lifecycle,
                    "imports": [
                        {"source": imp["source"], "names": imp["named"], "default": imp.get("default")}
                        for imp in imports
                        if not imp["source"].startswith(".")
                    ][:15],
                    "local_imports": [
                        imp["source"]
                        for imp in imports
                        if imp["source"].startswith(".")
                    ],
                    "style": style_info,
                    "uses_typescript": uses_ts,
                }

        if component_name and not tree:
            return {"status": "error", "message": f"Component '{component_name}' not found"}

        return {
            "status": "ok",
            "components": tree,
            "total": len(tree),
            "scanned_dirs": [os.path.relpath(d, base) for d in component_dirs],
        }

    # ── 2. get_route_map ──────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse SvelteKit file-based routing: src/routes/**/+page.svelte,
        +page.ts, +page.server.ts, +server.ts, +layout.svelte, +error.svelte.
        Extract route tree with load functions and actions.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return {"status": "error", "message": "src/routes directory not found"}

        routes: Dict[str, Dict[str, Any]] = {}

        for root, dirs, files in os.walk(routes_dir):
            route_path = self._extract_route_from_path(
                os.path.join(root, "placeholder"), routes_dir
            )

            if filter_prefix and not route_path.startswith(filter_prefix):
                continue

            route_info: Dict[str, Any] = {
                "path": route_path,
                "files": {},
                "has_page": False,
                "has_layout": False,
                "has_error": False,
                "has_server_endpoint": False,
                "load_functions": [],
                "form_actions": [],
                "api_methods": [],
                "params": [],
            }

            # Extract params from route path
            for m in re.finditer(r"\[([^\]]+)\]", route_path):
                param = m.group(1)
                is_rest = param.startswith("...")
                is_optional = param.startswith("[") or route_path.count("[[") > 0
                route_info["params"].append({
                    "name": param.lstrip(".").lstrip("[").rstrip("]"),
                    "rest": is_rest,
                    "optional": is_optional,
                })

            for fname in files:
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, base)

                if fname == "+page.svelte":
                    route_info["has_page"] = True
                    route_info["files"]["page"] = rel_path

                elif fname in ("+page.ts", "+page.js"):
                    route_info["files"]["page_load"] = rel_path
                    content = self._read_file(fpath)
                    if content:
                        load_info = self._parse_load_function(content, "universal")
                        if load_info:
                            route_info["load_functions"].append(load_info)

                elif fname in ("+page.server.ts", "+page.server.js"):
                    route_info["files"]["page_server"] = rel_path
                    content = self._read_file(fpath)
                    if content:
                        load_info = self._parse_load_function(content, "server")
                        if load_info:
                            route_info["load_functions"].append(load_info)
                        actions = self._parse_form_actions(content)
                        route_info["form_actions"] = actions

                elif fname in ("+server.ts", "+server.js"):
                    route_info["has_server_endpoint"] = True
                    route_info["files"]["server"] = rel_path
                    content = self._read_file(fpath)
                    if content:
                        methods = self._parse_api_methods(content)
                        route_info["api_methods"] = methods

                elif fname == "+layout.svelte":
                    route_info["has_layout"] = True
                    route_info["files"]["layout"] = rel_path

                elif fname in ("+layout.ts", "+layout.js"):
                    route_info["files"]["layout_load"] = rel_path
                    content = self._read_file(fpath)
                    if content:
                        load_info = self._parse_load_function(content, "universal-layout")
                        if load_info:
                            route_info["load_functions"].append(load_info)

                elif fname in ("+layout.server.ts", "+layout.server.js"):
                    route_info["files"]["layout_server"] = rel_path
                    content = self._read_file(fpath)
                    if content:
                        load_info = self._parse_load_function(content, "server-layout")
                        if load_info:
                            route_info["load_functions"].append(load_info)

                elif fname == "+error.svelte":
                    route_info["has_error"] = True
                    route_info["files"]["error"] = rel_path

            # Only add routes that have at least one SvelteKit file
            if route_info["files"]:
                routes[route_path] = route_info

        return {
            "status": "ok",
            "routes": routes,
            "total": len(routes),
            "pages": sum(1 for r in routes.values() if r["has_page"]),
            "layouts": sum(1 for r in routes.values() if r["has_layout"]),
            "api_endpoints": sum(1 for r in routes.values() if r["has_server_endpoint"]),
            "error_boundaries": sum(1 for r in routes.values() if r["has_error"]),
        }

    def _parse_load_function(self, content: str, load_type: str) -> Optional[Dict[str, Any]]:
        """Parse a load function from +page.ts / +page.server.ts / +layout.ts etc."""
        load_info: Dict[str, Any] = {"type": load_type}

        # Check if load is exported
        has_load = bool(re.search(
            r"export\s+(?:const|function|async\s+function)\s+load\b",
            content,
        ))
        if not has_load:
            return None

        load_info["exported"] = True

        # Extract fetch calls
        fetch_calls: List[str] = []
        for m in re.finditer(r"fetch\s*\(\s*['\"`]([^'\"`]+)['\"`]", content):
            fetch_calls.append(m.group(1))
        load_info["fetch_calls"] = fetch_calls

        # Extract depends() calls
        depends: List[str] = []
        for m in re.finditer(r"depends\s*\(\s*['\"]([^'\"]+)['\"]", content):
            depends.append(m.group(1))
        load_info["depends"] = depends

        # Check for parent() call
        load_info["calls_parent"] = bool(re.search(r"\bawait\s+parent\s*\(\s*\)", content))

        # Extract returned data keys
        return_keys: List[str] = []
        return_match = re.search(r"return\s*\{([^}]+)\}", content, re.DOTALL)
        if return_match:
            for m in re.finditer(r"(\w+)\s*(?::|,|\})", return_match.group(1)):
                return_keys.append(m.group(1))
        load_info["returns"] = return_keys

        # Check for error/redirect
        load_info["throws_error"] = bool(re.search(r"\berror\s*\(", content))
        load_info["throws_redirect"] = bool(re.search(r"\bredirect\s*\(", content))

        return load_info

    def _parse_form_actions(self, content: str) -> List[Dict[str, Any]]:
        """Parse form actions from +page.server.ts."""
        actions: List[Dict[str, Any]] = []

        # Check for actions export
        actions_match = re.search(
            r"export\s+const\s+actions\s*(?::\s*Actions)?\s*=\s*\{(.*)\}\s*(?:satisfies|;|$)",
            content,
            re.DOTALL,
        )
        if not actions_match:
            return actions

        actions_body = actions_match.group(1)

        # Extract action names: default: async ..., named: async ...
        action_pattern = re.compile(
            r"(\w+)\s*:\s*async\s*\(",
        )
        for m in action_pattern.finditer(actions_body):
            action_name = m.group(1)
            # Find the action body (approximate — get text until next action or end)
            start = m.end()
            # Find approximate end
            next_action = action_pattern.search(actions_body, start)
            end = next_action.start() if next_action else len(actions_body)
            action_body = actions_body[start:end]

            action_info: Dict[str, Any] = {
                "name": action_name,
                "is_default": action_name == "default",
            }

            # Check for form data parsing
            form_fields: List[str] = []
            for fm in re.finditer(r"(?:formData|data)\.get\s*\(\s*['\"](\w+)['\"]", action_body):
                form_fields.append(fm.group(1))
            for fm in re.finditer(r"(?:formData|data)\.getAll\s*\(\s*['\"](\w+)['\"]", action_body):
                form_fields.append(fm.group(1) + "[]")
            action_info["form_fields"] = form_fields

            # Check for fail() returns
            action_info["has_fail"] = bool(re.search(r"\bfail\s*\(", action_body))

            # Check for redirect
            action_info["has_redirect"] = bool(re.search(r"\bredirect\s*\(", action_body))

            # Check for validation
            action_info["has_validation"] = bool(
                re.search(r"(?:zod|schema|validate|parse|safeParse)\b", action_body, re.IGNORECASE)
            )

            actions.append(action_info)

        return actions

    def _parse_api_methods(self, content: str) -> List[Dict[str, Any]]:
        """Parse HTTP method handlers from +server.ts."""
        methods: List[Dict[str, Any]] = []
        http_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

        for method in http_methods:
            # export const GET: RequestHandler = async ...
            # export async function GET(...)
            pattern = re.compile(
                rf"export\s+(?:const\s+{method}\s*(?::\s*RequestHandler)?\s*=\s*(?:async\s*)?\(|"
                rf"async\s+function\s+{method}\s*\(|"
                rf"function\s+{method}\s*\()",
            )
            if pattern.search(content):
                method_info: Dict[str, Any] = {
                    "method": method,
                }

                # Check for json() response
                method_info["returns_json"] = bool(
                    re.search(r"json\s*\(|Response\.json\s*\(|new\s+Response\s*\(.*application/json", content)
                )

                # Check for auth
                method_info["has_auth_check"] = bool(
                    re.search(r"locals\.\w*(?:user|session|auth)|getSession|validateSession", content)
                )

                # Check for error handling
                method_info["has_error_handling"] = bool(
                    re.search(r"\berror\s*\(|try\s*\{|catch\s*\(", content)
                )

                methods.append(method_info)

        return methods

    # ── 3. get_store_map ──────────────────────────────────────────────

    def get_store_map(self, store_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Svelte stores: writable(), readable(), derived().
        Scan for custom store files. Map store -> subscriber ($store usage).
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        stores: Dict[str, Dict[str, Any]] = {}

        # Scan for store definitions in src/lib
        scan_dirs = [
            os.path.join(base, "src", "lib", "stores"),
            os.path.join(base, "src", "lib"),
            os.path.join(base, "src", "stores"),
        ]

        for scan_dir in scan_dirs:
            for fpath in self._walk_files(scan_dir, (".ts", ".js")):
                content = self._read_file(fpath)
                if not content:
                    continue

                rel_path = os.path.relpath(fpath, base)

                # writable(initialValue)
                for m in re.finditer(
                    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*writable\s*(?:<[^>]+>)?\s*\(([^)]*)\)",
                    content,
                ):
                    name = m.group(1)
                    if store_name and name.lower() != store_name.lower():
                        continue
                    initial = m.group(2).strip()
                    stores[name] = {
                        "type": "writable",
                        "file": rel_path,
                        "initial_value": initial[:80] if initial else None,
                        "exported": bool(re.search(rf"export\s+(?:const|let)\s+{re.escape(name)}\b", content)),
                        "subscribers": [],
                    }

                # readable(initialValue, start)
                for m in re.finditer(
                    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*readable\s*(?:<[^>]+>)?\s*\(",
                    content,
                ):
                    name = m.group(1)
                    if store_name and name.lower() != store_name.lower():
                        continue
                    stores[name] = {
                        "type": "readable",
                        "file": rel_path,
                        "exported": bool(re.search(rf"export\s+(?:const|let)\s+{re.escape(name)}\b", content)),
                        "subscribers": [],
                    }

                # derived(stores, fn)
                for m in re.finditer(
                    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*derived\s*(?:<[^>]+>)?\s*\(\s*(\[?[\w,\s]+\]?)",
                    content,
                ):
                    name = m.group(1)
                    if store_name and name.lower() != store_name.lower():
                        continue
                    source_stores_raw = m.group(2).strip().strip("[]")
                    source_stores = [s.strip() for s in source_stores_raw.split(",") if s.strip()]
                    stores[name] = {
                        "type": "derived",
                        "file": rel_path,
                        "source_stores": source_stores,
                        "exported": bool(re.search(rf"export\s+(?:const|let)\s+{re.escape(name)}\b", content)),
                        "subscribers": [],
                    }

                # Custom store pattern: function createXxxStore() { ... writable ... }
                for m in re.finditer(
                    r"(?:export\s+)?function\s+(\w+Store\w*)\s*\(",
                    content,
                ):
                    name = m.group(1)
                    if store_name and name.lower() != store_name.lower():
                        continue
                    if name not in stores:
                        stores[name] = {
                            "type": "custom",
                            "file": rel_path,
                            "exported": bool(re.search(rf"export\s+function\s+{re.escape(name)}\b", content)),
                            "subscribers": [],
                        }

        # Now scan all .svelte files for $store subscriptions
        svelte_dirs = [
            os.path.join(base, "src", "lib", "components"),
            os.path.join(base, "src", "lib"),
            os.path.join(base, "src", "routes"),
        ]

        for scan_dir in svelte_dirs:
            for fpath in self._walk_files(scan_dir, (".svelte",)):
                content = self._read_file(fpath)
                if not content:
                    continue

                rel_path = os.path.relpath(fpath, base)

                for s_name in stores:
                    # Check for $storeName usage
                    if re.search(rf"\${re.escape(s_name)}\b", content):
                        stores[s_name]["subscribers"].append(rel_path)
                    # Also check for .subscribe() call
                    elif re.search(rf"{re.escape(s_name)}\.subscribe\s*\(", content):
                        stores[s_name]["subscribers"].append(rel_path)

        if store_name and not stores:
            return {"status": "error", "message": f"Store '{store_name}' not found"}

        return {
            "status": "ok",
            "stores": stores,
            "total": len(stores),
            "writable_count": sum(1 for s in stores.values() if s["type"] == "writable"),
            "readable_count": sum(1 for s in stores.values() if s["type"] == "readable"),
            "derived_count": sum(1 for s in stores.values() if s["type"] == "derived"),
            "custom_count": sum(1 for s in stores.values() if s["type"] == "custom"),
        }

    # ── 4. get_component_dependencies ─────────────────────────────────

    def get_component_dependencies(self, component_path: str) -> Dict[str, Any]:
        """
        For a .svelte file: parent importers, child components, props, slots,
        events dispatched, stores subscribed, context provided/consumed.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, component_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {component_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        script = self._extract_script_block(content) or ""
        module_script = self._extract_script_block(content, module=True) or ""
        template = self._extract_template_block(content)

        component_name = os.path.basename(full_path).replace(".svelte", "")

        result: Dict[str, Any] = {
            "file": component_path,
            "component_name": component_name,
            "props": self._extract_props(script),
            "events": self._extract_events(script, template),
            "slots": self._extract_slots(template),
            "children_components": self._extract_child_components(template),
            "store_subscriptions": self._extract_store_subscriptions(content),
            "context": self._extract_context_usage(script + module_script),
            "reactive_statements": self._extract_reactive_statements(script),
            "imports": self._extract_imports(script + module_script),
            "parent_importers": [],
        }

        # Find parent importers — files that import this component
        scan_dirs = [
            os.path.join(base, "src", "lib"),
            os.path.join(base, "src", "routes"),
        ]

        for scan_dir in scan_dirs:
            for fpath in self._walk_files(scan_dir, (".svelte",)):
                if fpath == full_path:
                    continue
                fcontent = self._read_file(fpath)
                if not fcontent:
                    continue

                # Check if this file imports our component
                if re.search(
                    rf"import\s+{re.escape(component_name)}\s+from\s+['\"]",
                    fcontent,
                ):
                    rel = os.path.relpath(fpath, base)
                    # Extract how it's used (what props are passed)
                    passed_props: List[str] = []
                    for pm in re.finditer(
                        rf"<{re.escape(component_name)}\s+([^>]+)",
                        fcontent,
                    ):
                        attrs = pm.group(1)
                        for ap in re.finditer(r"(\w+)\s*=", attrs):
                            passed_props.append(ap.group(1))
                        # Also check bind:prop
                        for ap in re.finditer(r"bind:(\w+)", attrs):
                            passed_props.append(f"bind:{ap.group(1)}")

                    # Extract event listeners
                    listened_events: List[str] = []
                    for em in re.finditer(
                        rf"<{re.escape(component_name)}[^>]*on:(\w+)",
                        fcontent,
                    ):
                        listened_events.append(em.group(1))

                    result["parent_importers"].append({
                        "file": rel,
                        "passed_props": passed_props,
                        "listened_events": listened_events,
                    })

        return {"status": "ok", **result}

    # ── 5. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> hooks (handle/handleError/handleFetch) ->
        layout load -> page load -> server load -> form action -> component render.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return {"status": "error", "message": "src/routes directory not found"}

        # Resolve entry point — could be a file path or route path
        target_route: Optional[str] = None
        target_dir: Optional[str] = None

        if entry_point.startswith("/"):
            # It's a route path — find matching directory
            target_route = entry_point
            for root, dirs, files in os.walk(routes_dir):
                route = self._extract_route_from_path(
                    os.path.join(root, "placeholder"), routes_dir
                )
                if route == entry_point:
                    target_dir = root
                    break
        else:
            # It's a file path
            full = os.path.join(base, entry_point)
            if os.path.isfile(full):
                target_dir = os.path.dirname(full)
                target_route = self._extract_route_from_path(full, routes_dir)
            elif os.path.isdir(full):
                target_dir = full
                target_route = self._extract_route_from_path(
                    os.path.join(full, "placeholder"), routes_dir
                )

        if not target_dir or not target_route:
            return {"status": "error", "message": f"Could not resolve entry point: {entry_point}"}

        flow: Dict[str, Any] = {
            "route": target_route,
            "steps": [],
            "warnings": [],
        }

        # Step 1: Hooks
        hooks_server = os.path.join(base, "src", "hooks.server.ts")
        if not os.path.isfile(hooks_server):
            hooks_server = os.path.join(base, "src", "hooks.server.js")

        if os.path.isfile(hooks_server):
            hook_content = self._read_file(hooks_server)
            if hook_content:
                hook_step: Dict[str, Any] = {
                    "step": "hooks.server",
                    "file": os.path.relpath(hooks_server, base),
                    "handlers": [],
                }
                if re.search(r"export\s+(?:const|async\s+function)\s+handle\b", hook_content):
                    handle_info: Dict[str, Any] = {"name": "handle"}
                    # Check for sequence()
                    if re.search(r"sequence\s*\(", hook_content):
                        handle_info["uses_sequence"] = True
                        # Extract sequence handlers
                        seq_match = re.search(r"sequence\s*\(([^)]+)\)", hook_content)
                        if seq_match:
                            handlers = [h.strip() for h in seq_match.group(1).split(",") if h.strip()]
                            handle_info["sequence_handlers"] = handlers
                    # Check for auth checks
                    if re.search(r"(?:session|user|auth|token)", hook_content, re.IGNORECASE):
                        handle_info["has_auth"] = True
                    # Check for redirects
                    if re.search(r"\bredirect\s*\(", hook_content):
                        handle_info["has_redirect"] = True
                    hook_step["handlers"].append(handle_info)

                if re.search(r"export\s+(?:const|async\s+function)\s+handleError\b", hook_content):
                    hook_step["handlers"].append({"name": "handleError"})

                if re.search(r"export\s+(?:const|async\s+function)\s+handleFetch\b", hook_content):
                    hook_step["handlers"].append({"name": "handleFetch"})

                if hook_step["handlers"]:
                    flow["steps"].append(hook_step)

        # Step 2: Layout chain (walk from root to target)
        current = routes_dir
        rel_to_target = os.path.relpath(target_dir, routes_dir)
        parts = [] if rel_to_target == "." else rel_to_target.split(os.sep)

        layout_chain: List[Dict[str, Any]] = []
        for i in range(len(parts) + 1):
            check_dir = os.path.join(routes_dir, *parts[:i]) if i > 0 else routes_dir

            for layout_file in ["+layout.server.ts", "+layout.server.js",
                                "+layout.ts", "+layout.js", "+layout.svelte"]:
                layout_path = os.path.join(check_dir, layout_file)
                if os.path.isfile(layout_path):
                    layout_content = self._read_file(layout_path)
                    layout_info: Dict[str, Any] = {
                        "file": os.path.relpath(layout_path, base),
                        "type": layout_file,
                    }
                    if layout_content and "load" in layout_file.replace(".svelte", ""):
                        load = self._parse_load_function(layout_content, "layout")
                        if load:
                            layout_info["load"] = load
                    layout_chain.append(layout_info)

        if layout_chain:
            flow["steps"].append({
                "step": "layout_chain",
                "layouts": layout_chain,
            })

        # Step 3: Page load functions
        for load_file in ["+page.server.ts", "+page.server.js", "+page.ts", "+page.js"]:
            load_path = os.path.join(target_dir, load_file)
            if os.path.isfile(load_path):
                load_content = self._read_file(load_path)
                if load_content:
                    load_type = "server" if "server" in load_file else "universal"
                    load_info = self._parse_load_function(load_content, load_type)
                    if load_info:
                        flow["steps"].append({
                            "step": f"page_load ({load_type})",
                            "file": os.path.relpath(load_path, base),
                            "load": load_info,
                        })

        # Step 4: Form actions (if method is POST or not specified)
        if not method or method.upper() == "POST":
            for action_file in ["+page.server.ts", "+page.server.js"]:
                action_path = os.path.join(target_dir, action_file)
                if os.path.isfile(action_path):
                    action_content = self._read_file(action_path)
                    if action_content:
                        actions = self._parse_form_actions(action_content)
                        if actions:
                            flow["steps"].append({
                                "step": "form_actions",
                                "file": os.path.relpath(action_path, base),
                                "actions": actions,
                            })

        # Step 5: Server endpoint (if it's an API route)
        for server_file in ["+server.ts", "+server.js"]:
            server_path = os.path.join(target_dir, server_file)
            if os.path.isfile(server_path):
                server_content = self._read_file(server_path)
                if server_content:
                    api_methods = self._parse_api_methods(server_content)
                    if method:
                        api_methods = [m for m in api_methods if m["method"] == method.upper()]
                    if api_methods:
                        flow["steps"].append({
                            "step": "server_endpoint",
                            "file": os.path.relpath(server_path, base),
                            "methods": api_methods,
                        })

        # Step 6: Page component
        for page_file in ["+page.svelte"]:
            page_path = os.path.join(target_dir, page_file)
            if os.path.isfile(page_path):
                page_content = self._read_file(page_path)
                if page_content:
                    template = self._extract_template_block(page_content)
                    children = self._extract_child_components(template)
                    script = self._extract_script_block(page_content) or ""
                    store_subs = self._extract_store_subscriptions(page_content)

                    flow["steps"].append({
                        "step": "page_render",
                        "file": os.path.relpath(page_path, base),
                        "child_components": children,
                        "store_subscriptions": store_subs,
                        "uses_form_enhance": bool(re.search(r"use:enhance", page_content)),
                    })

        if not flow["steps"]:
            flow["warnings"].append("No SvelteKit files found for this route")

        return {"status": "ok", **flow}

    # ── 6. get_load_functions ─────────────────────────────────────────

    def get_load_functions(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze all load functions: +page.ts (universal), +page.server.ts (server-only),
        +layout.ts, +layout.server.ts. Extract: fetch calls, depends(), parent(), data returned.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return {"status": "error", "message": "src/routes directory not found"}

        load_files = [
            "+page.ts", "+page.js", "+page.server.ts", "+page.server.js",
            "+layout.ts", "+layout.js", "+layout.server.ts", "+layout.server.js",
        ]

        all_loads: List[Dict[str, Any]] = []

        for root, dirs, files in os.walk(routes_dir):
            route = self._extract_route_from_path(
                os.path.join(root, "placeholder"), routes_dir
            )

            if route_path and route != route_path:
                continue

            for fname in files:
                if fname not in load_files:
                    continue

                fpath = os.path.join(root, fname)
                content = self._read_file(fpath)
                if not content:
                    continue

                # Determine load type
                if "server" in fname:
                    if "layout" in fname:
                        load_type = "server-layout"
                    else:
                        load_type = "server"
                else:
                    if "layout" in fname:
                        load_type = "universal-layout"
                    else:
                        load_type = "universal"

                load_info = self._parse_load_function(content, load_type)
                if load_info:
                    load_info["route"] = route
                    load_info["file"] = os.path.relpath(fpath, base)

                    # Additional analysis: cookies, locals, platform
                    if "server" in load_type:
                        load_info["uses_cookies"] = bool(re.search(r"\bcookies\b", content))
                        load_info["uses_locals"] = bool(re.search(r"\blocals\b", content))
                        load_info["uses_platform"] = bool(re.search(r"\bplatform\b", content))

                    # Check for streaming (promises in return)
                    load_info["has_streaming"] = bool(
                        re.search(r"return\s*\{[^}]*(?:promise|stream|defer)\b", content, re.DOTALL)
                    )

                    all_loads.append(load_info)

        if route_path and not all_loads:
            return {"status": "error", "message": f"No load functions found for route: {route_path}"}

        return {
            "status": "ok",
            "load_functions": all_loads,
            "total": len(all_loads),
            "server_loads": sum(1 for l in all_loads if "server" in l["type"]),
            "universal_loads": sum(1 for l in all_loads if "universal" in l["type"]),
            "layout_loads": sum(1 for l in all_loads if "layout" in l["type"]),
        }

    # ── 7. get_middleware_chain ────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse hooks.server.ts (handle sequence), hooks.client.ts,
        +layout.server.ts auth checks. Trace middleware applied to each route.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "hooks_server": None,
            "hooks_client": None,
            "layout_guards": [],
            "route_specific": [],
        }

        # Parse hooks.server.ts
        for ext in (".ts", ".js"):
            hooks_path = os.path.join(base, "src", f"hooks.server{ext}")
            if os.path.isfile(hooks_path):
                content = self._read_file(hooks_path)
                if content:
                    hooks_info: Dict[str, Any] = {
                        "file": os.path.relpath(hooks_path, base),
                        "handlers": [],
                    }

                    # handle function
                    if re.search(r"export\s+(?:const|async\s+function)\s+handle\b", content):
                        handle: Dict[str, Any] = {"name": "handle"}

                        # Check for sequence()
                        seq_match = re.search(r"sequence\s*\(([^)]+)\)", content)
                        if seq_match:
                            handle["uses_sequence"] = True
                            handlers = [h.strip() for h in seq_match.group(1).split(",") if h.strip()]
                            handle["sequence_handlers"] = handlers
                        else:
                            handle["uses_sequence"] = False

                        # Auth-related checks
                        auth_patterns: List[str] = []
                        if re.search(r"event\.locals\.\w*(?:user|session|auth)", content):
                            auth_patterns.append("sets event.locals auth data")
                        if re.search(r"\bredirect\s*\(", content):
                            auth_patterns.append("redirects on auth failure")
                        if re.search(r"event\.url\.pathname", content):
                            auth_patterns.append("checks pathname for protected routes")
                        if re.search(r"(?:cookie|token|jwt|bearer)", content, re.IGNORECASE):
                            auth_patterns.append("reads auth cookie/token")
                        handle["auth_patterns"] = auth_patterns

                        # URL matching patterns
                        url_checks: List[str] = []
                        for m in re.finditer(
                            r"event\.url\.pathname\.startsWith\s*\(\s*['\"]([^'\"]+)['\"]",
                            content,
                        ):
                            url_checks.append(m.group(1))
                        for m in re.finditer(
                            r"event\.url\.pathname\s*===?\s*['\"]([^'\"]+)['\"]",
                            content,
                        ):
                            url_checks.append(m.group(1))
                        handle["url_checks"] = url_checks

                        # CORS headers
                        handle["sets_cors"] = bool(
                            re.search(r"Access-Control-Allow", content)
                        )

                        hooks_info["handlers"].append(handle)

                    # handleError
                    if re.search(r"export\s+(?:const|async\s+function)\s+handleError\b", content):
                        error_handler: Dict[str, Any] = {"name": "handleError"}
                        error_handler["reports_to_service"] = bool(
                            re.search(r"(?:sentry|bugsnag|rollbar|datadog|logflare)", content, re.IGNORECASE)
                        )
                        hooks_info["handlers"].append(error_handler)

                    # handleFetch
                    if re.search(r"export\s+(?:const|async\s+function)\s+handleFetch\b", content):
                        hooks_info["handlers"].append({"name": "handleFetch"})

                    result["hooks_server"] = hooks_info
                break

        # Parse hooks.client.ts
        for ext in (".ts", ".js"):
            hooks_path = os.path.join(base, "src", f"hooks.client{ext}")
            if os.path.isfile(hooks_path):
                content = self._read_file(hooks_path)
                if content:
                    client_hooks: Dict[str, Any] = {
                        "file": os.path.relpath(hooks_path, base),
                        "handlers": [],
                    }
                    if re.search(r"export\s+(?:const|async\s+function)\s+handleError\b", content):
                        client_hooks["handlers"].append({"name": "handleError"})
                    result["hooks_client"] = client_hooks
                break

        # Scan layout.server files for auth guards
        routes_dir = self._find_routes_dir(base)
        if routes_dir:
            for root, dirs, files in os.walk(routes_dir):
                for fname in files:
                    if fname not in ("+layout.server.ts", "+layout.server.js"):
                        continue

                    fpath = os.path.join(root, fname)
                    content = self._read_file(fpath)
                    if not content:
                        continue

                    route = self._extract_route_from_path(fpath, routes_dir)
                    rel = os.path.relpath(fpath, base)

                    guard_info: Dict[str, Any] = {
                        "file": rel,
                        "route_prefix": route,
                    }

                    # Check for auth checks
                    has_auth = bool(re.search(
                        r"(?:locals\.(?:user|session|auth)|redirect\s*\(\s*\d+|throw\s+error\s*\()",
                        content,
                    ))
                    guard_info["has_auth_check"] = has_auth

                    # Check for redirect
                    redirect_match = re.search(r"redirect\s*\(\s*(\d+)\s*,\s*['\"]([^'\"]+)['\"]", content)
                    if redirect_match:
                        guard_info["redirect_status"] = int(redirect_match.group(1))
                        guard_info["redirect_target"] = redirect_match.group(2)

                    if has_auth:
                        result["layout_guards"].append(guard_info)

            # Route-specific middleware (if route_path specified)
            if route_path:
                for root, dirs, files in os.walk(routes_dir):
                    route = self._extract_route_from_path(
                        os.path.join(root, "placeholder"), routes_dir
                    )
                    if route != route_path:
                        continue

                    for fname in files:
                        if not fname.startswith("+"):
                            continue
                        fpath = os.path.join(root, fname)
                        content = self._read_file(fpath)
                        if not content:
                            continue

                        rel = os.path.relpath(fpath, base)

                        # Check for auth/guard patterns
                        if re.search(r"(?:locals|session|auth|redirect|error\s*\()", content):
                            result["route_specific"].append({
                                "file": rel,
                                "route": route,
                                "has_auth": bool(re.search(r"locals\.\w*(?:user|session|auth)", content)),
                                "has_redirect": bool(re.search(r"\bredirect\s*\(", content)),
                            })

        return {"status": "ok", **result}

    # ── 8. get_form_actions ───────────────────────────────────────────

    def get_form_actions(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse +page.server.ts actions: export const actions = { default, named }.
        Extract form data parsing, validation, fail() returns, redirect().
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return {"status": "error", "message": "src/routes directory not found"}

        all_actions: List[Dict[str, Any]] = []

        for root, dirs, files in os.walk(routes_dir):
            route = self._extract_route_from_path(
                os.path.join(root, "placeholder"), routes_dir
            )

            if route_path and route != route_path:
                continue

            for fname in files:
                if fname not in ("+page.server.ts", "+page.server.js"):
                    continue

                fpath = os.path.join(root, fname)
                content = self._read_file(fpath)
                if not content:
                    continue

                actions = self._parse_form_actions(content)
                if not actions:
                    continue

                rel = os.path.relpath(fpath, base)

                # Also check the corresponding +page.svelte for form usage
                page_svelte = os.path.join(root, "+page.svelte")
                form_info: Dict[str, Any] = {
                    "route": route,
                    "file": rel,
                    "actions": actions,
                }

                if os.path.isfile(page_svelte):
                    page_content = self._read_file(page_svelte)
                    if page_content:
                        form_info["page_uses_enhance"] = bool(
                            re.search(r"use:enhance", page_content)
                        )
                        # Extract form fields from the page
                        page_fields: List[str] = []
                        for m in re.finditer(
                            r'<input[^>]+name\s*=\s*["\'](\w+)["\']',
                            page_content,
                        ):
                            page_fields.append(m.group(1))
                        for m in re.finditer(
                            r'<select[^>]+name\s*=\s*["\'](\w+)["\']',
                            page_content,
                        ):
                            page_fields.append(m.group(1))
                        for m in re.finditer(
                            r'<textarea[^>]+name\s*=\s*["\'](\w+)["\']',
                            page_content,
                        ):
                            page_fields.append(m.group(1))
                        form_info["page_form_fields"] = page_fields

                        # Cross-reference: check mismatches between page fields and action fields
                        for action in actions:
                            action_fields = set(
                                f.rstrip("[]") for f in action.get("form_fields", [])
                            )
                            page_field_set = set(page_fields)
                            missing_in_page = action_fields - page_field_set
                            missing_in_action = page_field_set - action_fields
                            if missing_in_page:
                                action["warnings"] = action.get("warnings", [])
                                action["warnings"].append(
                                    f"Fields parsed in action but not in form: {sorted(missing_in_page)}"
                                )
                            if missing_in_action:
                                action["warnings"] = action.get("warnings", [])
                                action["warnings"].append(
                                    f"Fields in form but not parsed in action: {sorted(missing_in_action)}"
                                )

                all_actions.append(form_info)

        if route_path and not all_actions:
            return {"status": "error", "message": f"No form actions found for route: {route_path}"}

        return {
            "status": "ok",
            "form_actions": all_actions,
            "total_routes_with_actions": len(all_actions),
            "total_actions": sum(len(a["actions"]) for a in all_actions),
        }

    # ── 9. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check: route file exists (+page.svelte or +server.ts), load function returns data,
        form actions handle POST, error boundaries present.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return {"status": "error", "message": "src/routes directory not found"}

        method = method.upper()
        result: Dict[str, Any] = {
            "method": method,
            "url": url,
            "checks": [],
            "warnings": [],
            "errors": [],
        }

        # Find matching route directory
        target_dir: Optional[str] = None
        for root, dirs, files in os.walk(routes_dir):
            route = self._extract_route_from_path(
                os.path.join(root, "placeholder"), routes_dir
            )
            if route == url:
                target_dir = root
                break

        if not target_dir:
            # Try to match dynamic routes
            for root, dirs, files in os.walk(routes_dir):
                route = self._extract_route_from_path(
                    os.path.join(root, "placeholder"), routes_dir
                )
                # Convert route pattern to regex
                route_regex = re.sub(r"\[\.\.\.(\w+)\]", r"(?P<\1>.+)", route)
                route_regex = re.sub(r"\[(\w+)\]", r"(?P<\1>[^/]+)", route_regex)
                if re.fullmatch(route_regex, url):
                    target_dir = root
                    result["checks"].append({
                        "check": "route_match",
                        "passed": True,
                        "detail": f"Matched dynamic route pattern: {route}",
                    })
                    break

        if not target_dir:
            result["errors"].append(f"No route found matching URL: {url}")
            result["checks"].append({
                "check": "route_exists",
                "passed": False,
                "detail": f"No directory in src/routes matches {url}",
            })
            return {"status": "error", **result}

        result["checks"].append({
            "check": "route_exists",
            "passed": True,
            "detail": f"Route directory: {os.path.relpath(target_dir, base)}",
        })

        # Check for +server.ts (API endpoint)
        has_server = False
        for ext in (".ts", ".js"):
            server_path = os.path.join(target_dir, f"+server{ext}")
            if os.path.isfile(server_path):
                has_server = True
                content = self._read_file(server_path)
                if content:
                    # Check if the method is exported
                    method_exported = bool(re.search(
                        rf"export\s+(?:const\s+{method}|async\s+function\s+{method}|function\s+{method})\b",
                        content,
                    ))
                    result["checks"].append({
                        "check": f"{method}_handler",
                        "passed": method_exported,
                        "detail": f"{method} handler {'exported' if method_exported else 'NOT exported'} in +server{ext}",
                    })
                    if not method_exported:
                        result["errors"].append(f"{method} handler not exported in +server{ext}")

                    # Check for error handling
                    has_error = bool(re.search(r"try\s*\{|catch\s*\(|error\s*\(", content))
                    result["checks"].append({
                        "check": "error_handling",
                        "passed": has_error,
                        "detail": f"Error handling {'present' if has_error else 'MISSING'}",
                    })
                    if not has_error:
                        result["warnings"].append("No error handling detected in server endpoint")

                    # Check for auth
                    has_auth = bool(re.search(r"locals\.\w*(?:user|session|auth)", content))
                    result["checks"].append({
                        "check": "auth_check",
                        "passed": has_auth,
                        "detail": f"Auth check {'present' if has_auth else 'not found (may be in hooks)'}",
                    })
                break

        # Check for +page.svelte
        has_page = os.path.isfile(os.path.join(target_dir, "+page.svelte"))
        if has_page:
            result["checks"].append({
                "check": "page_exists",
                "passed": True,
                "detail": "+page.svelte found",
            })

        if not has_server and not has_page:
            result["errors"].append("Neither +server.ts nor +page.svelte found")

        # Check for load function (GET requests or page rendering)
        if method == "GET" or has_page:
            has_load = False
            for load_file in ["+page.server.ts", "+page.server.js", "+page.ts", "+page.js"]:
                load_path = os.path.join(target_dir, load_file)
                if os.path.isfile(load_path):
                    content = self._read_file(load_path)
                    if content and re.search(r"export\s+(?:const|function|async\s+function)\s+load\b", content):
                        has_load = True
                        load_info = self._parse_load_function(content, "page")
                        result["checks"].append({
                            "check": "load_function",
                            "passed": True,
                            "detail": f"Load function found in {load_file}",
                            "returns": load_info.get("returns", []) if load_info else [],
                        })
                    break
            if not has_load and has_page:
                result["warnings"].append("Page has no load function — data must come from layout or stores")

        # Check for form actions (POST)
        if method == "POST" and has_page:
            for action_file in ["+page.server.ts", "+page.server.js"]:
                action_path = os.path.join(target_dir, action_file)
                if os.path.isfile(action_path):
                    content = self._read_file(action_path)
                    if content:
                        actions = self._parse_form_actions(content)
                        if actions:
                            result["checks"].append({
                                "check": "form_actions",
                                "passed": True,
                                "detail": f"Form actions found: {[a['name'] for a in actions]}",
                            })
                        else:
                            result["warnings"].append("POST to page but no form actions found in +page.server")
                    break

        # Check for error boundary
        has_error_boundary = False
        check_dir = target_dir
        while check_dir.startswith(routes_dir):
            if os.path.isfile(os.path.join(check_dir, "+error.svelte")):
                has_error_boundary = True
                result["checks"].append({
                    "check": "error_boundary",
                    "passed": True,
                    "detail": f"+error.svelte found at {os.path.relpath(check_dir, base)}",
                })
                break
            parent = os.path.dirname(check_dir)
            if parent == check_dir:
                break
            check_dir = parent

        if not has_error_boundary:
            result["warnings"].append("No +error.svelte boundary found in route hierarchy")

        return {"status": "ok", **result}

    # ── 10. get_project_conventions ───────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from a SvelteKit project codebase.
        Patterns: naming, state, styling, testing, loading.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_types = ["naming", "state", "styling", "testing", "loading"]
        if pattern_type not in valid_types:
            return {"status": "error", "message": f"Invalid pattern_type. Use one of: {valid_types}"}

        conventions: Dict[str, Any] = {"pattern_type": pattern_type}

        if pattern_type == "naming":
            conventions.update(self._analyze_naming_conventions(base))
        elif pattern_type == "state":
            conventions.update(self._analyze_state_conventions(base))
        elif pattern_type == "styling":
            conventions.update(self._analyze_styling_conventions(base))
        elif pattern_type == "testing":
            conventions.update(self._analyze_testing_conventions(base))
        elif pattern_type == "loading":
            conventions.update(self._analyze_loading_conventions(base))

        return {"status": "ok", **conventions}

    def _analyze_naming_conventions(self, base: str) -> Dict[str, Any]:
        """Analyze file and component naming conventions."""
        result: Dict[str, Any] = {
            "component_naming": {"PascalCase": 0, "kebab-case": 0, "camelCase": 0, "other": 0},
            "file_naming": {"PascalCase": 0, "kebab-case": 0, "camelCase": 0, "other": 0},
            "uses_typescript": False,
            "examples": [],
        }

        lib_dir = os.path.join(base, "src", "lib")
        ts_count = 0
        js_count = 0

        for fpath in self._walk_files(lib_dir, (".svelte", ".ts", ".js")):
            fname = os.path.basename(fpath)
            name = fname.split(".")[0]

            if fname.endswith(".ts"):
                ts_count += 1
            elif fname.endswith(".js"):
                js_count += 1

            if fname.endswith(".svelte"):
                # Component file naming
                if re.match(r"^[A-Z][a-zA-Z0-9]+$", name):
                    result["component_naming"]["PascalCase"] += 1
                elif re.match(r"^[a-z]+(-[a-z]+)+$", name):
                    result["component_naming"]["kebab-case"] += 1
                elif re.match(r"^[a-z][a-zA-Z0-9]+$", name):
                    result["component_naming"]["camelCase"] += 1
                else:
                    result["component_naming"]["other"] += 1
            else:
                # Non-component file naming
                if re.match(r"^[A-Z][a-zA-Z0-9]+$", name):
                    result["file_naming"]["PascalCase"] += 1
                elif re.match(r"^[a-z]+(-[a-z]+)*$", name):
                    result["file_naming"]["kebab-case"] += 1
                elif re.match(r"^[a-z][a-zA-Z0-9]+$", name):
                    result["file_naming"]["camelCase"] += 1
                else:
                    result["file_naming"]["other"] += 1

        result["uses_typescript"] = ts_count > js_count

        # Dominant component naming
        max_style = max(result["component_naming"], key=result["component_naming"].get)
        result["dominant_component_style"] = max_style

        # Dominant file naming
        max_file_style = max(result["file_naming"], key=result["file_naming"].get)
        result["dominant_file_style"] = max_file_style

        return result

    def _analyze_state_conventions(self, base: str) -> Dict[str, Any]:
        """Analyze state management conventions."""
        result: Dict[str, Any] = {
            "stores": {"writable": 0, "readable": 0, "derived": 0, "custom": 0},
            "context_usage": 0,
            "load_function_data": 0,
            "page_store_usage": 0,
            "dominant_pattern": None,
        }

        # Scan stores
        store_dirs = [
            os.path.join(base, "src", "lib", "stores"),
            os.path.join(base, "src", "lib"),
        ]
        for scan_dir in store_dirs:
            for fpath in self._walk_files(scan_dir, (".ts", ".js")):
                content = self._read_file(fpath)
                if not content:
                    continue
                result["stores"]["writable"] += len(re.findall(r"\bwritable\s*\(", content))
                result["stores"]["readable"] += len(re.findall(r"\breadable\s*\(", content))
                result["stores"]["derived"] += len(re.findall(r"\bderived\s*\(", content))
                result["stores"]["custom"] += len(re.findall(r"function\s+\w+Store\s*\(", content))

        # Scan for context usage and load data usage
        src_dir = os.path.join(base, "src")
        for fpath in self._walk_files(src_dir, (".svelte", ".ts", ".js")):
            content = self._read_file(fpath)
            if not content:
                continue
            result["context_usage"] += len(re.findall(r"(?:getContext|setContext)\s*\(", content))
            result["load_function_data"] += len(re.findall(
                r"export\s+(?:const|function|async\s+function)\s+load\b", content
            ))
            result["page_store_usage"] += len(re.findall(r"\$page\.", content))

        # Determine dominant pattern
        total_stores = sum(result["stores"].values())
        if total_stores > result["context_usage"] and total_stores > result["load_function_data"]:
            result["dominant_pattern"] = "svelte-stores"
        elif result["load_function_data"] > total_stores:
            result["dominant_pattern"] = "load-functions"
        elif result["context_usage"] > 0:
            result["dominant_pattern"] = "context-api"
        else:
            result["dominant_pattern"] = "unknown"

        return result

    def _analyze_styling_conventions(self, base: str) -> Dict[str, Any]:
        """Analyze styling conventions."""
        result: Dict[str, Any] = {
            "scoped_styles": 0,
            "global_styles": 0,
            "tailwind": False,
            "tailwind_in_components": 0,
            "css_modules": 0,
            "scss": 0,
            "postcss": False,
            "dominant_pattern": None,
        }

        # Check for Tailwind config
        for config_name in ["tailwind.config.js", "tailwind.config.ts", "tailwind.config.cjs"]:
            if os.path.isfile(os.path.join(base, config_name)):
                result["tailwind"] = True
                break

        # Check for PostCSS
        for config_name in ["postcss.config.js", "postcss.config.cjs"]:
            if os.path.isfile(os.path.join(base, config_name)):
                result["postcss"] = True
                break

        # Scan components
        src_dir = os.path.join(base, "src")
        for fpath in self._walk_files(src_dir, (".svelte",)):
            content = self._read_file(fpath)
            if not content:
                continue

            style = self._extract_style_block(content)
            if style is not None:
                result["scoped_styles"] += 1
                if re.search(r":global\s*\(", style):
                    result["global_styles"] += 1
                if re.search(r"@apply\b", style):
                    result["tailwind_in_components"] += 1

            # Check for Tailwind class usage in template
            template = self._extract_template_block(content)
            if re.search(r'class\s*=\s*["\'][^"\']*(?:flex|grid|p-|m-|text-|bg-|w-|h-)', template):
                result["tailwind_in_components"] += 1

            if re.search(r'lang\s*=\s*["\']scss["\']', content):
                result["scss"] += 1

        # Determine dominant
        if result["tailwind"] and result["tailwind_in_components"] > result["scoped_styles"]:
            result["dominant_pattern"] = "tailwind"
        elif result["scoped_styles"] > 0:
            result["dominant_pattern"] = "scoped-styles"
        elif result["scss"] > 0:
            result["dominant_pattern"] = "scss"
        else:
            result["dominant_pattern"] = "unknown"

        return result

    def _analyze_testing_conventions(self, base: str) -> Dict[str, Any]:
        """Analyze testing conventions."""
        result: Dict[str, Any] = {
            "vitest": False,
            "playwright": False,
            "testing_library": False,
            "unit_test_count": 0,
            "e2e_test_count": 0,
            "test_file_patterns": [],
            "dominant_framework": None,
        }

        # Check package.json for test deps
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            content = self._read_file(pkg_path)
            if content:
                try:
                    pkg = json.loads(content)
                    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    result["vitest"] = "vitest" in all_deps
                    result["playwright"] = "@playwright/test" in all_deps or "playwright" in all_deps
                    result["testing_library"] = any(
                        k.startswith("@testing-library") for k in all_deps
                    )
                except json.JSONDecodeError as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

        # Count test files
        tests_dir = os.path.join(base, "tests")
        e2e_dir = os.path.join(base, "e2e")
        src_dir = os.path.join(base, "src")

        for fpath in self._walk_files(tests_dir, (".ts", ".js")):
            result["unit_test_count"] += 1

        for fpath in self._walk_files(e2e_dir, (".ts", ".js")):
            result["e2e_test_count"] += 1

        # Also check for co-located test files
        for fpath in self._walk_files(src_dir, (".ts", ".js")):
            fname = os.path.basename(fpath)
            if re.match(r".*\.(test|spec)\.(ts|js)$", fname):
                result["unit_test_count"] += 1
                if ".test." in fname:
                    if "*.test.*" not in result["test_file_patterns"]:
                        result["test_file_patterns"].append("*.test.*")
                if ".spec." in fname:
                    if "*.spec.*" not in result["test_file_patterns"]:
                        result["test_file_patterns"].append("*.spec.*")

        # Determine dominant
        if result["vitest"] and result["playwright"]:
            result["dominant_framework"] = "vitest + playwright"
        elif result["vitest"]:
            result["dominant_framework"] = "vitest"
        elif result["playwright"]:
            result["dominant_framework"] = "playwright"
        else:
            result["dominant_framework"] = "none detected"

        return result

    def _analyze_loading_conventions(self, base: str) -> Dict[str, Any]:
        """Analyze data loading conventions."""
        result: Dict[str, Any] = {
            "server_loads": 0,
            "universal_loads": 0,
            "streaming": 0,
            "prerender_pages": 0,
            "ssr_disabled": 0,
            "fetch_in_load": 0,
            "direct_db_in_load": 0,
            "depends_usage": 0,
            "invalidate_usage": 0,
            "dominant_pattern": None,
        }

        routes_dir = self._find_routes_dir(base)
        if not routes_dir:
            return result

        for fpath in self._walk_files(routes_dir, (".ts", ".js")):
            content = self._read_file(fpath)
            if not content:
                continue

            fname = os.path.basename(fpath)

            if "server" in fname:
                if re.search(r"export\s+(?:const|function|async\s+function)\s+load\b", content):
                    result["server_loads"] += 1
            elif fname.startswith("+page.") or fname.startswith("+layout."):
                if re.search(r"export\s+(?:const|function|async\s+function)\s+load\b", content):
                    result["universal_loads"] += 1

            # Streaming
            if re.search(r"(?:stream|defer|promise)\b.*return", content, re.DOTALL):
                result["streaming"] += 1

            # Prerender
            if re.search(r"export\s+(?:const|let)\s+prerender\s*=\s*true", content):
                result["prerender_pages"] += 1

            # SSR disabled
            if re.search(r"export\s+(?:const|let)\s+ssr\s*=\s*false", content):
                result["ssr_disabled"] += 1

            # Fetch in load
            if re.search(r"\bfetch\s*\(", content):
                result["fetch_in_load"] += 1

            # Direct DB (prisma, drizzle, etc)
            if re.search(r"(?:prisma|db|drizzle)\.\w+\.", content):
                result["direct_db_in_load"] += 1

            # depends
            result["depends_usage"] += len(re.findall(r"\bdepends\s*\(", content))

        # Scan for invalidate usage
        src_dir = os.path.join(base, "src")
        for fpath in self._walk_files(src_dir, (".svelte", ".ts", ".js")):
            content = self._read_file(fpath)
            if content:
                result["invalidate_usage"] += len(re.findall(r"\binvalidate(?:All)?\s*\(", content))

        # Dominant pattern
        if result["server_loads"] > result["universal_loads"]:
            result["dominant_pattern"] = "server-load"
        elif result["universal_loads"] > result["server_loads"]:
            result["dominant_pattern"] = "universal-load"
        elif result["direct_db_in_load"] > 0:
            result["dominant_pattern"] = "direct-db-access"
        else:
            result["dominant_pattern"] = "mixed"

        return result

    # ── 11. get_similar_patterns ──────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.
        Registry: modal, form-action, auth-guard, toast, dropdown, table,
        pagination, search, layout, error-boundary.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        description_lower = description.lower()

        # Score each pattern against the description
        matched_patterns: List[Tuple[str, float]] = []
        for pattern_name, pattern_def in self.PATTERN_REGISTRY.items():
            score = 0.0
            for kw in pattern_def["keywords"]:
                if kw in description_lower:
                    score += 2.0
            # Partial matches
            for kw in pattern_def["keywords"]:
                for word in description_lower.split():
                    if kw.startswith(word) or word.startswith(kw):
                        score += 0.5
            if score > 0:
                matched_patterns.append((pattern_name, score))

        # Sort by score
        matched_patterns.sort(key=lambda x: x[1], reverse=True)

        if not matched_patterns:
            # Fallback: try all patterns
            matched_patterns = [(name, 0.5) for name in self.PATTERN_REGISTRY]

        # Take top 3 pattern types
        top_patterns = matched_patterns[:3]

        results: List[Dict[str, Any]] = []
        src_dir = os.path.join(base, "src")
        all_files = self._walk_files(src_dir, (".svelte", ".ts", ".js"))

        for pattern_name, score in top_patterns:
            pattern_def = self.PATTERN_REGISTRY[pattern_name]
            matches: List[Dict[str, Any]] = []

            for fpath in all_files:
                fname = os.path.basename(fpath).lower()
                content = self._read_file(fpath)
                if not content:
                    continue

                rel_path = os.path.relpath(fpath, base)
                file_score = 0.0

                # Check file name patterns
                for fp in pattern_def["file_patterns"]:
                    if re.search(fp, fname, re.IGNORECASE):
                        file_score += 2.0

                # Check code patterns
                matched_code_patterns: List[str] = []
                for cp in pattern_def["code_patterns"]:
                    if re.search(cp, content):
                        file_score += 1.0
                        matched_code_patterns.append(cp)

                if file_score >= 1.0:
                    # Extract relevant code snippets
                    snippets: List[str] = []
                    lines = content.split("\n")
                    for cp in matched_code_patterns[:3]:
                        for i, line in enumerate(lines):
                            if re.search(cp, line):
                                snippets.append(f"L{i + 1}: {line.strip()[:100]}")
                                break

                    matches.append({
                        "file": rel_path,
                        "score": file_score,
                        "matched_patterns": matched_code_patterns[:5],
                        "snippets": snippets[:3],
                    })

            # Sort matches by score
            matches.sort(key=lambda x: x["score"], reverse=True)

            if matches:
                results.append({
                    "pattern": pattern_name,
                    "relevance_score": score,
                    "matches": matches[:10],
                    "total_matches": len(matches),
                })

        return {
            "status": "ok",
            "query": description,
            "patterns_found": results,
            "total_patterns": len(results),
        }

    # ── 12. get_project_snapshot ──────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview: routes, components, stores, hooks, lib utilities,
        svelte.config settings, environment.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {}

        # package.json
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            pkg_content = self._read_file(pkg_path)
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    snapshot["project_name"] = pkg.get("name", "unknown")
                    snapshot["scripts"] = list(pkg.get("scripts", {}).keys())
                    deps = pkg.get("dependencies", {})
                    dev_deps = pkg.get("devDependencies", {})

                    key_deps: Dict[str, str] = {}
                    for dep in [
                        "@sveltejs/kit", "svelte", "@sveltejs/adapter-auto",
                        "@sveltejs/adapter-node", "@sveltejs/adapter-vercel",
                        "@sveltejs/adapter-netlify", "@sveltejs/adapter-static",
                        "typescript", "tailwindcss", "prisma", "@prisma/client",
                        "drizzle-orm", "lucia", "zod", "superforms",
                        "sveltekit-superforms", "vitest", "@playwright/test",
                        "skeleton", "@skeletonlabs/skeleton",
                        "svelte-headlessui", "bits-ui", "melt-ui",
                    ]:
                        if dep in deps:
                            key_deps[dep] = deps[dep]
                        elif dep in dev_deps:
                            key_deps[dep] = dev_deps[dep] + " (dev)"

                    snapshot["key_dependencies"] = key_deps
                    snapshot["total_dependencies"] = len(deps)
                    snapshot["total_dev_dependencies"] = len(dev_deps)
                except json.JSONDecodeError as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

        # svelte.config.js
        for config_name in ["svelte.config.js", "svelte.config.ts"]:
            config_path = os.path.join(base, config_name)
            if os.path.isfile(config_path):
                config_content = self._read_file(config_path)
                if config_content:
                    config_info: Dict[str, Any] = {"file": config_name}
                    # Adapter
                    adapter_match = re.search(r"adapter\s*:\s*(\w+)\s*\(", config_content)
                    if adapter_match:
                        config_info["adapter"] = adapter_match.group(1)
                    # Preprocess
                    if re.search(r"preprocess", config_content):
                        config_info["has_preprocessor"] = True
                    # Alias
                    alias_count = len(re.findall(r"['\"](\$\w+)['\"]", config_content))
                    if alias_count:
                        config_info["alias_count"] = alias_count

                    snapshot["svelte_config"] = config_info
                break

        # TypeScript
        snapshot["has_typescript"] = os.path.isfile(os.path.join(base, "tsconfig.json"))

        # Routes summary
        routes_dir = self._find_routes_dir(base)
        if routes_dir:
            pages = 0
            layouts = 0
            server_endpoints = 0
            error_boundaries = 0
            form_action_routes = 0
            route_list: List[str] = []

            for root, dirs, files in os.walk(routes_dir):
                route = self._extract_route_from_path(
                    os.path.join(root, "placeholder"), routes_dir
                )
                has_sveltekit_files = False

                for fname in files:
                    if fname == "+page.svelte":
                        pages += 1
                        has_sveltekit_files = True
                    elif fname == "+layout.svelte":
                        layouts += 1
                    elif fname in ("+server.ts", "+server.js"):
                        server_endpoints += 1
                        has_sveltekit_files = True
                    elif fname == "+error.svelte":
                        error_boundaries += 1
                    elif fname in ("+page.server.ts", "+page.server.js"):
                        fpath = os.path.join(root, fname)
                        content = self._read_file(fpath)
                        if content and re.search(r"export\s+const\s+actions", content):
                            form_action_routes += 1

                if has_sveltekit_files and route not in route_list:
                    route_list.append(route)

            snapshot["routes"] = {
                "pages": pages,
                "layouts": layouts,
                "server_endpoints": server_endpoints,
                "error_boundaries": error_boundaries,
                "form_action_routes": form_action_routes,
                "route_list": sorted(route_list)[:30],
            }

        # Components
        component_counts: Dict[str, int] = {}
        lib_components = os.path.join(base, "src", "lib", "components")
        lib_dir = os.path.join(base, "src", "lib")

        for cdir in [lib_components, lib_dir]:
            if not os.path.isdir(cdir):
                continue
            count = 0
            for fpath in self._walk_files(cdir, (".svelte",)):
                fname = os.path.basename(fpath)
                if not fname.startswith("+"):
                    count += 1
            if count > 0:
                component_counts[os.path.relpath(cdir, base)] = count

        snapshot["components"] = component_counts
        snapshot["total_components"] = sum(component_counts.values())

        # Stores
        store_count = 0
        store_dirs = [
            os.path.join(base, "src", "lib", "stores"),
            os.path.join(base, "src", "stores"),
        ]
        for sdir in store_dirs:
            for fpath in self._walk_files(sdir, (".ts", ".js")):
                content = self._read_file(fpath)
                if content and re.search(r"(?:writable|readable|derived)\s*\(", content):
                    store_count += 1
        snapshot["store_files"] = store_count

        # Hooks
        hooks: List[str] = []
        for hook_file in ["hooks.server.ts", "hooks.server.js", "hooks.client.ts", "hooks.client.js"]:
            if os.path.isfile(os.path.join(base, "src", hook_file)):
                hooks.append(hook_file)
        snapshot["hooks"] = hooks

        # Lib utilities
        lib_dir = self._find_lib_dir(base)
        if lib_dir:
            util_files = 0
            for fpath in self._walk_files(lib_dir, (".ts", ".js")):
                fname = os.path.basename(fpath)
                if not fname.startswith("+"):
                    util_files += 1
            snapshot["lib_utility_files"] = util_files

        # Params
        params_dir = os.path.join(base, "src", "params")
        if os.path.isdir(params_dir):
            param_files = [
                os.path.basename(f).replace(".ts", "").replace(".js", "")
                for f in self._walk_files(params_dir, (".ts", ".js"))
            ]
            snapshot["param_matchers"] = param_files

        # Environment files
        env_files: List[str] = []
        for env_name in [".env", ".env.local", ".env.development", ".env.production"]:
            if os.path.isfile(os.path.join(base, env_name)):
                env_files.append(env_name)
        snapshot["env_files"] = env_files

        # Check for app.d.ts (type declarations)
        app_dts = os.path.join(base, "src", "app.d.ts")
        if os.path.isfile(app_dts):
            content = self._read_file(app_dts)
            if content:
                locals_match = re.search(r"interface\s+Locals\s*\{([^}]*)\}", content, re.DOTALL)
                if locals_match:
                    local_fields: List[str] = []
                    for m in re.finditer(r"(\w+)\s*:", locals_match.group(1)):
                        local_fields.append(m.group(1))
                    snapshot["app_locals"] = local_fields

                page_data_match = re.search(r"interface\s+PageData\s*\{([^}]*)\}", content, re.DOTALL)
                if page_data_match:
                    page_fields: List[str] = []
                    for m in re.finditer(r"(\w+)\s*:", page_data_match.group(1)):
                        page_fields.append(m.group(1))
                    snapshot["app_page_data"] = page_fields

        return {"status": "ok", **snapshot}

    # ── 13. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit impact analysis:
        - +page -> check load/layout
        - component -> check importers
        - store -> check subscribers
        - hook -> check affected routes
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name,
            "file_type": None,
            "impact": [],
            "warnings": [],
            "suggestions": [],
        }

        # Determine file type
        fname = os.path.basename(full_path)
        if fname == "+page.svelte":
            result["file_type"] = "page"
        elif fname == "+layout.svelte":
            result["file_type"] = "layout"
        elif fname in ("+page.ts", "+page.js"):
            result["file_type"] = "page-load"
        elif fname in ("+page.server.ts", "+page.server.js"):
            result["file_type"] = "page-server"
        elif fname in ("+layout.server.ts", "+layout.server.js"):
            result["file_type"] = "layout-server"
        elif fname in ("+layout.ts", "+layout.js"):
            result["file_type"] = "layout-load"
        elif fname in ("+server.ts", "+server.js"):
            result["file_type"] = "server-endpoint"
        elif fname == "+error.svelte":
            result["file_type"] = "error-boundary"
        elif fname in ("hooks.server.ts", "hooks.server.js"):
            result["file_type"] = "hooks-server"
        elif fname in ("hooks.client.ts", "hooks.client.js"):
            result["file_type"] = "hooks-client"
        elif fname.endswith(".svelte"):
            result["file_type"] = "component"
        elif re.search(r"(?:writable|readable|derived)\s*\(", content):
            result["file_type"] = "store"
        else:
            result["file_type"] = "utility"

        # Find all files that reference the symbol
        src_dir = os.path.join(base, "src")
        all_files = self._walk_files(src_dir, (".svelte", ".ts", ".js"))
        referencing_files: List[Dict[str, Any]] = []

        for fpath in all_files:
            if fpath == full_path:
                continue

            fcontent = self._read_file(fpath)
            if not fcontent or symbol_name not in fcontent:
                continue

            rel = os.path.relpath(fpath, base)

            usages: List[Dict[str, Any]] = []
            lines = fcontent.split("\n")
            for i, line in enumerate(lines, 1):
                if symbol_name not in line:
                    continue

                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("<!--"):
                    continue

                usage_type = "reference"
                if re.search(rf"import\s+.*\b{re.escape(symbol_name)}\b.*from", stripped):
                    usage_type = "import"
                elif re.search(rf"<{re.escape(symbol_name)}[\s/>]", stripped):
                    usage_type = "component-usage"
                elif re.search(rf"\${re.escape(symbol_name)}\b", stripped):
                    usage_type = "store-subscription"
                elif re.search(rf"{re.escape(symbol_name)}\s*\(", stripped):
                    usage_type = "call"
                elif re.search(rf"export\s+let\s+{re.escape(symbol_name)}\b", stripped):
                    usage_type = "prop-declaration"
                elif re.search(rf"bind:{re.escape(symbol_name)}\b", stripped):
                    usage_type = "bind"
                elif re.search(rf"on:{re.escape(symbol_name)}\b", stripped):
                    usage_type = "event-listener"

                usages.append({"line": i, "type": usage_type, "text": stripped[:120]})

            if usages:
                referencing_files.append({
                    "file": rel,
                    "usages": usages[:5],
                    "count": len(usages),
                })

        result["referencing_files"] = referencing_files[:25]
        result["total_references"] = sum(r["count"] for r in referencing_files)

        # Type-specific impact analysis
        if result["file_type"] == "component":
            # Check if symbol is a prop
            script = self._extract_script_block(content) or ""
            props = self._extract_props(script)
            prop_names = [p["name"] for p in props]
            if symbol_name in prop_names:
                result["impact"].append({
                    "type": "prop-change",
                    "message": f"'{symbol_name}' is a component prop -- all parent components passing this prop will be affected",
                    "affected_files": [
                        r["file"] for r in referencing_files
                        if any(u["type"] == "component-usage" for u in r["usages"])
                    ],
                })

            # Check if it's an event
            template = self._extract_template_block(content)
            events = self._extract_events(script, template)
            if symbol_name in events:
                result["impact"].append({
                    "type": "event-change",
                    "message": f"'{symbol_name}' is a dispatched event -- all parent components listening to on:{symbol_name} will be affected",
                    "affected_files": [
                        r["file"] for r in referencing_files
                        if any(u["type"] == "event-listener" for u in r["usages"])
                    ],
                })

            # Component rename
            comp_name = fname.replace(".svelte", "")
            if comp_name == symbol_name:
                result["impact"].append({
                    "type": "component-rename",
                    "message": f"Renaming component '{symbol_name}' -- update all imports and template usages",
                    "affected_count": len(referencing_files),
                })

        elif result["file_type"] == "store":
            result["impact"].append({
                "type": "store-change",
                "message": f"Store symbol '{symbol_name}' change -- all components using ${symbol_name} will be affected",
                "affected_files": [
                    r["file"] for r in referencing_files
                    if any(u["type"] == "store-subscription" for u in r["usages"])
                ],
            })
            result["warnings"].append("Store changes can have cascading effects through derived stores")

        elif result["file_type"] in ("page-load", "page-server"):
            # Check what route this belongs to
            routes_dir = self._find_routes_dir(base)
            if routes_dir:
                route = self._extract_route_from_path(full_path, routes_dir)
                result["impact"].append({
                    "type": "load-function-change",
                    "message": f"Load function change for route '{route}' -- the corresponding +page.svelte data access may be affected",
                    "route": route,
                })
                # Check if data key is being changed
                if re.search(rf"return\s*\{{[^}}]*\b{re.escape(symbol_name)}\b", content, re.DOTALL):
                    result["warnings"].append(
                        f"'{symbol_name}' appears to be a returned data key -- "
                        f"check +page.svelte for $page.data.{symbol_name} or data.{symbol_name} usage"
                    )

        elif result["file_type"] == "layout-server":
            routes_dir = self._find_routes_dir(base)
            if routes_dir:
                route = self._extract_route_from_path(full_path, routes_dir)
                # Count child routes
                layout_dir = os.path.dirname(full_path)
                child_routes = 0
                for root, dirs, files in os.walk(layout_dir):
                    if any(f.startswith("+page") for f in files):
                        child_routes += 1
                result["impact"].append({
                    "type": "layout-change",
                    "message": f"Layout load change at '{route}' -- affects {child_routes} child route(s)",
                    "child_routes": child_routes,
                })
                result["warnings"].append("Layout load changes cascade to ALL child routes and pages")

        elif result["file_type"] == "hooks-server":
            result["impact"].append({
                "type": "hooks-change",
                "message": "hooks.server change affects ALL server-side requests",
            })
            result["warnings"].append("This is a global hook -- changes affect every route in the application")
            result["suggestions"].append("Test thoroughly with multiple routes before deploying")

        elif result["file_type"] == "server-endpoint":
            routes_dir = self._find_routes_dir(base)
            if routes_dir:
                route = self._extract_route_from_path(full_path, routes_dir)
                # Find all fetch() calls to this route
                fetch_callers: List[str] = []
                for fpath in all_files:
                    fcontent = self._read_file(fpath)
                    if fcontent and re.search(rf"fetch\s*\(\s*['\"`]{re.escape(route)}", fcontent):
                        fetch_callers.append(os.path.relpath(fpath, base))
                result["impact"].append({
                    "type": "api-endpoint-change",
                    "message": f"API endpoint change at '{route}'",
                    "fetch_callers": fetch_callers[:15],
                })

        elif result["file_type"] == "layout":
            routes_dir = self._find_routes_dir(base)
            if routes_dir:
                layout_dir = os.path.dirname(full_path)
                child_count = 0
                for root, dirs, files in os.walk(layout_dir):
                    if any(f == "+page.svelte" for f in files):
                        child_count += 1
                result["impact"].append({
                    "type": "layout-template-change",
                    "message": f"Layout template change -- visually affects {child_count} child page(s)",
                    "child_pages": child_count,
                })

        return {"status": "ok", **result}

    # ── verify_schema ────────────────────────────────────────────────

    def verify_schema(self, model_name: str, fields: list) -> Dict[str, Any]:
        """Verify data model fields across Prisma schema and TypeScript types in $lib/types."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "model": model_name,
            "expected_fields": fields,
            "prisma_fields": [],
            "typescript_fields": [],
            "mismatches": [],
        }

        # Scan Prisma schema
        prisma_paths = [
            os.path.join(base, "prisma", "schema.prisma"),
            os.path.join(base, "src", "lib", "prisma", "schema.prisma"),
        ]
        for prisma_path in prisma_paths:
            if os.path.isfile(prisma_path):
                content = self._read_file(prisma_path) or ""
                model_match = re.search(
                    rf"model\s+{re.escape(model_name)}\s*\{{(.*?)\}}",
                    content, re.DOTALL,
                )
                if model_match:
                    body = model_match.group(1)
                    for line in body.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("//") and not line.startswith("@@"):
                            field_m = re.match(r"(\w+)\s+", line)
                            if field_m:
                                result["prisma_fields"].append(field_m.group(1))

        # Scan TypeScript types in $lib/types
        ts_dirs = [
            os.path.join(base, "src", "lib", "types"),
            os.path.join(base, "src", "lib"),
            os.path.join(base, "src", "types"),
        ]
        iface_pattern = re.compile(
            rf"(?:interface|type)\s+{re.escape(model_name)}\s*(?:extends\s+\w+\s*)?\{{(.*?)\}}",
            re.DOTALL,
        )
        ts_field_pattern = re.compile(r"(\w+)\s*[?:]")

        for tdir in ts_dirs:
            if not os.path.isdir(tdir):
                continue
            for fpath in self._walk_files(tdir, (".ts", ".d.ts")):
                content = self._read_file(fpath) or ""
                for im in iface_pattern.finditer(content):
                    body = im.group(1)
                    for fm in ts_field_pattern.finditer(body):
                        fname = fm.group(1)
                        if fname not in result["typescript_fields"]:
                            result["typescript_fields"].append(fname)

        # Check mismatches
        expected_set = set(fields)
        prisma_set = set(result["prisma_fields"])
        ts_set = set(result["typescript_fields"])

        for field in fields:
            if prisma_set and field not in prisma_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_prisma",
                    "message": f"Field '{field}' not found in Prisma schema",
                })
            if ts_set and field not in ts_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_typescript",
                    "message": f"Field '{field}' not found in TypeScript types",
                })

        for pf in prisma_set - expected_set:
            result["mismatches"].append({
                "field": pf, "issue": "extra_in_prisma",
                "message": f"Field '{pf}' in Prisma schema but not in expected fields",
            })

        result["status"] = "ok" if not result["mismatches"] else "mismatches_found"
        return result

    # ── detect_transaction_risks ─────────────────────────────────────

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing transactions in +page.server.ts form actions with multiple DB writes."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        risks: List[Dict[str, Any]] = []

        # Find form actions (SvelteKit pattern)
        action_pattern = re.compile(
            r"(?:export\s+const\s+actions\s*(?::\s*Actions)?\s*=\s*\{|"
            r"(\w+)\s*:\s*(?:async\s*)?\([^)]*\)\s*(?:=>|:\s*RequestHandler))",
            re.MULTILINE,
        )

        # Find all async functions / action handlers
        func_pattern = re.compile(
            r"(?:async\s+)?(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*async\s*\()",
        )
        functions = list(func_pattern.finditer(content))

        for idx, fm in enumerate(functions):
            func_name = fm.group(1) or fm.group(2) or fm.group(3) or "anonymous"
            start = fm.start()
            end = functions[idx + 1].start() if idx + 1 < len(functions) else len(content)
            body = content[start:end]

            # Count DB write operations (prisma or drizzle)
            prisma_writes = re.findall(
                r"(?:prisma|db)\s*\.\s*\w+\s*\.\s*(?:create|update|delete|upsert|createMany|updateMany|deleteMany)\s*\(",
                body,
            )
            drizzle_writes = re.findall(
                r"(?:db|drizzle)\s*\.\s*(?:insert|update|delete)\s*\(",
                body,
            )
            total_writes = len(prisma_writes) + len(drizzle_writes)

            if total_writes >= 2:
                has_transaction = bool(re.search(
                    r"(?:prisma\s*\.\s*\$transaction|\.\s*transaction\s*\()",
                    body,
                ))
                if not has_transaction:
                    line_num = content[:start].count("\n") + 1
                    risks.append({
                        "action": func_name,
                        "line": line_num,
                        "write_operations": total_writes,
                        "risk": "multiple_writes_without_transaction",
                        "message": f"Action '{func_name}' has {total_writes} DB write operations without transaction",
                        "suggestion": "Wrap in prisma.$transaction() or db.transaction()",
                    })

        # Check for form actions specifically in +page.server.ts
        if "+page.server" in file_path:
            actions_match = re.search(
                r"export\s+const\s+actions\s*(?::\s*Actions)?\s*=\s*\{(.*)\}\s*(?:satisfies\s+Actions)?",
                content, re.DOTALL,
            )
            if actions_match:
                actions_body = actions_match.group(1)
                # Find individual actions
                action_names = re.findall(r"(\w+)\s*:\s*async", actions_body)
                for aname in action_names:
                    action_block_match = re.search(
                        rf"{re.escape(aname)}\s*:\s*async\s*\([^)]*\)\s*=>\s*\{{(.*?)(?:\}}\s*,|\}}\s*$)",
                        actions_body, re.DOTALL,
                    )
                    if action_block_match:
                        ablock = action_block_match.group(1)
                        writes = len(re.findall(
                            r"(?:prisma|db)\s*\.\s*(?:\w+\s*\.\s*)?(?:create|update|delete|insert)\s*\(",
                            ablock,
                        ))
                        if writes >= 2 and "$transaction" not in ablock and ".transaction" not in ablock:
                            risks.append({
                                "action": aname,
                                "risk": "form_action_no_transaction",
                                "write_operations": writes,
                                "message": f"Form action '{aname}' has {writes} writes without transaction wrapping",
                            })

        return {"status": "ok", "file": file_path, "risks": risks, "total_risks": len(risks)}

    # ── get_domain_rules ─────────────────────────────────────────────

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check SvelteKit domain-specific rules for file types."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        violations: List[Dict[str, Any]] = []
        file_type = "unknown"
        fname = os.path.basename(full_path)

        if fname == "+page.server.ts" or fname == "+page.server.js":
            file_type = "page_server"
            # Rule: form actions require validation
            actions_match = re.search(r"export\s+const\s+actions", content)
            if actions_match:
                action_bodies = re.findall(
                    r"(\w+)\s*:\s*async\s*\([^)]*\)\s*=>\s*\{(.*?)\}(?:\s*,|\s*\})",
                    content, re.DOTALL,
                )
                for aname, abody in action_bodies:
                    has_validation = bool(re.search(
                        r"(?:zod|yup|valibot|superforms|validate|parse|safeParse)\s*[.(]",
                        abody, re.IGNORECASE,
                    ))
                    if not has_validation:
                        violations.append({
                            "rule": "action_requires_validation",
                            "severity": "warning",
                            "action": aname,
                            "message": f"Form action '{aname}' lacks input validation -- use zod, superforms, or manual validation",
                        })

            # Rule: load functions require error handling
            load_match = re.search(r"export\s+(?:const|async\s+function)\s+load", content)
            if load_match:
                load_body = content[load_match.start():]
                if "error" not in load_body[:500].lower() and "throw" not in load_body[:500]:
                    violations.append({
                        "rule": "load_requires_error_handling",
                        "severity": "warning",
                        "message": "Load function lacks error handling -- use error() from @sveltejs/kit",
                    })

        elif fname == "+server.ts" or fname == "+server.js":
            file_type = "api_server"
            # Rule: API endpoints require error responses
            http_methods = re.findall(
                r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|DELETE|PATCH)\b",
                content,
            )
            for method in http_methods:
                # Find the method body
                method_match = re.search(
                    rf"export\s+(?:async\s+)?(?:function|const)\s+{method}.*?(?=export\s+(?:async\s+)?(?:function|const)\s+(?:GET|POST|PUT|DELETE|PATCH)|\Z)",
                    content, re.DOTALL,
                )
                if method_match:
                    mbody = method_match.group(0)
                    has_error_response = bool(re.search(
                        r"(?:error\s*\(|new\s+Response\s*\(\s*(?:null|JSON\.stringify).*?[45]\d\d|json\s*\(.*?status\s*:\s*[45]\d\d)",
                        mbody,
                    ))
                    if not has_error_response:
                        violations.append({
                            "rule": "endpoint_requires_error_response",
                            "severity": "warning",
                            "method": method,
                            "message": f"{method} handler lacks error responses -- return proper HTTP error status codes",
                        })

        elif fname.startswith("+") and fname.endswith(".svelte"):
            file_type = "page_component"
            # Check if load data is being used without error handling in template
            if "{#if" not in content and "data" in content:
                has_data_access = bool(re.search(r"(?:export\s+let\s+data|\$page\.data)", content))
                if has_data_access:
                    violations.append({
                        "rule": "page_data_error_handling",
                        "severity": "info",
                        "message": "Page accesses load data without conditional rendering -- consider handling loading/error states",
                    })

        elif fname == "hooks.server.ts" or fname == "hooks.server.js":
            file_type = "hooks"
            # Rule: hooks should use sequence()
            handle_count = len(re.findall(r"export\s+(?:const|async\s+function)\s+handle\b", content))
            has_sequence = "sequence" in content
            if handle_count >= 1 and not has_sequence:
                # Check if there are multiple handle-like functions that should be composed
                handle_like = re.findall(r"(?:const|function)\s+(\w*handle\w*)\s*", content, re.IGNORECASE)
                if len(handle_like) >= 2:
                    violations.append({
                        "rule": "hooks_require_sequence",
                        "severity": "warning",
                        "message": f"Multiple handle functions found ({', '.join(handle_like[:5])}) -- use sequence() to compose them",
                    })

        return {
            "status": "ok",
            "file": file_path,
            "file_type": file_type,
            "violations": violations,
            "total_violations": len(violations),
        }

    # ── generate_test_skeleton ───────────────────────────────────────

    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Generate Vitest/Playwright test skeleton for SvelteKit components and load functions."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {"file": file_path, "symbol": symbol, "skeleton": "", "test_type": "unknown"}
        rel_path = os.path.relpath(full_path, base) if base else file_path
        fname = os.path.basename(full_path)

        # Extract Svelte component props
        props = re.findall(r"export\s+let\s+(\w+)", content)

        if fname.endswith(".svelte"):
            result["test_type"] = "component"
            props_str = ", ".join(f"{p}: undefined" for p in props[:5]) if props else ""

            # Determine route for Playwright test
            route_path = "/" + os.path.dirname(rel_path).replace("src/routes/", "").replace("src/routes", "")
            route_path = re.sub(r"\([^)]+\)/", "", route_path)  # Remove group layouts
            route_path = re.sub(r"\+page\.svelte$", "", route_path)

            result["skeleton"] = (
                f"// Vitest component test\n"
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ render, screen }} from '@testing-library/svelte';\n"
                f"import {symbol} from '$lib/{rel_path}';\n\n"
                f"describe('{symbol}', () => {{\n"
                f"  it('should render successfully', () => {{\n"
                f"    const {{ container }} = render({symbol}, {{ props: {{ {props_str} }} }});\n"
                f"    expect(container).toBeTruthy();\n"
                f"  }});\n"
                f"}});\n\n"
                f"// Playwright e2e test\n"
                f"import {{ test, expect }} from '@playwright/test';\n\n"
                f"test('{symbol} page', async ({{ page }}) => {{\n"
                f"  await page.goto('{route_path}');\n"
                f"  // TODO: Add specific page assertions\n"
                f"  await expect(page).toHaveTitle(/.*/);\n"
                f"}});\n"
            )

        elif fname.endswith((".ts", ".js")) and "+page.server" in fname:
            result["test_type"] = "load_function"
            result["skeleton"] = (
                f"import {{ describe, it, expect, vi }} from 'vitest';\n"
                f"import {{ load }} from './{fname}';\n\n"
                f"describe('{symbol} load function', () => {{\n"
                f"  it('should return expected data', async () => {{\n"
                f"    const mockEvent = {{\n"
                f"      params: {{}},\n"
                f"      url: new URL('http://localhost'),\n"
                f"      locals: {{}},\n"
                f"      fetch: vi.fn(),\n"
                f"      depends: vi.fn(),\n"
                f"      parent: vi.fn().mockResolvedValue({{}}),\n"
                f"    }};\n"
                f"    const result = await load(mockEvent as any);\n"
                f"    expect(result).toBeDefined();\n"
                f"    // TODO: Assert specific returned properties\n"
                f"  }});\n\n"
                f"  it('should handle errors gracefully', async () => {{\n"
                f"    // TODO: Test error scenarios\n"
                f"  }});\n"
                f"}});\n"
            )

        elif fname == "+server.ts" or fname == "+server.js":
            result["test_type"] = "api_endpoint"
            http_methods = re.findall(
                r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|DELETE|PATCH)",
                content,
            )
            method_tests = "\n\n".join(
                f"  it('should handle {m} requests', async () => {{\n"
                f"    const mockEvent = {{\n"
                f"      request: new Request('http://localhost', {{ method: '{m}' }}),\n"
                f"      params: {{}},\n"
                f"      locals: {{}},\n"
                f"      url: new URL('http://localhost'),\n"
                f"    }};\n"
                f"    const response = await {m}(mockEvent as any);\n"
                f"    expect(response.status).toBe(200);\n"
                f"  }});"
                for m in http_methods
            )
            imports = ", ".join(http_methods)
            result["skeleton"] = (
                f"import {{ describe, it, expect, vi }} from 'vitest';\n"
                f"import {{ {imports} }} from './{fname}';\n\n"
                f"describe('{symbol} API endpoint', () => {{\n"
                f"{method_tests}\n"
                f"}});\n"
            )
        else:
            result["test_type"] = "unit"
            result["skeleton"] = (
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ {symbol} }} from './{fname.replace('.ts', '').replace('.js', '')}';\n\n"
                f"describe('{symbol}', () => {{\n"
                f"  it('should work correctly', () => {{\n"
                f"    const result = {symbol}();\n"
                f"    expect(result).toBeDefined();\n"
                f"  }});\n"
                f"}});\n"
            )

        result["status"] = "ok"
        return result

    # ── match_view_guards ────────────────────────────────────────────

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match hooks.server.ts auth to {#if} conditions in .svelte files and +page.server.ts load auth."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol,
            "server_guards": [],
            "client_guards": [],
            "load_guards": [],
            "unmatched": [],
        }

        rel_path = os.path.relpath(full_path, base) if base else file_path
        fname = os.path.basename(full_path)

        # Check hooks.server.ts for auth handle
        if fname in ("hooks.server.ts", "hooks.server.js"):
            auth_patterns = re.findall(
                r"(?:event\.locals\.\w*(?:auth|user|session)\w*|"
                r"getSession|validateSession|verifyToken|isAuthenticated)\s*[=(]",
                content, re.IGNORECASE,
            )
            for ap in auth_patterns:
                result["server_guards"].append({
                    "type": "hooks_auth",
                    "check": ap.strip()[:60],
                    "file": rel_path,
                })

            redirect_guards = re.findall(
                r"(?:throw\s+redirect|redirect)\s*\(\s*(\d+)\s*,\s*['\"]([^'\"]+)['\"]",
                content,
            )
            for status, path in redirect_guards:
                result["server_guards"].append({
                    "type": "auth_redirect",
                    "status": status,
                    "redirect_to": path,
                })

        # Check +page.server.ts load functions for auth
        routes_dir = self._find_routes_dir(base)
        if routes_dir:
            for fpath in self._walk_files(routes_dir, (".ts", ".js")):
                if "+page.server" not in os.path.basename(fpath):
                    continue
                fcontent = self._read_file(fpath) or ""
                load_auth = re.findall(
                    r"(?:locals\.\w*(?:auth|user|session)\w*|getSession|validateSession)\s*[=(]",
                    fcontent, re.IGNORECASE,
                )
                if load_auth:
                    rel = os.path.relpath(fpath, base)
                    for la in load_auth:
                        result["load_guards"].append({
                            "type": "load_auth",
                            "check": la.strip()[:60],
                            "file": rel,
                        })

        # Scan .svelte files for {#if} auth conditionals
        src_dir = os.path.join(base, "src")
        scan_base = src_dir if os.path.isdir(src_dir) else base

        for fpath in self._walk_files(scan_base, (".svelte",)):
            svelte_content = self._read_file(fpath) or ""
            rel = os.path.relpath(fpath, base)

            # Look for auth-related {#if} conditions
            if_conditions = re.findall(
                r"\{#if\s+([^}]*(?:auth|user|logged|session|isAuth|token|role|permission)[^}]*)\}",
                svelte_content, re.IGNORECASE,
            )
            for cond in if_conditions:
                result["client_guards"].append({
                    "file": rel,
                    "condition": cond.strip()[:80],
                    "type": "svelte_if_block",
                })

            # Look for $page.data auth checks
            page_data_auth = re.findall(
                r"\$page\.data\.(\w*(?:auth|user|session)\w*)",
                svelte_content, re.IGNORECASE,
            )
            for pda in page_data_auth:
                result["client_guards"].append({
                    "file": rel,
                    "condition": f"$page.data.{pda}",
                    "type": "page_data_auth",
                })

        # Identify unmatched guards
        has_server = bool(result["server_guards"]) or bool(result["load_guards"])
        has_client = bool(result["client_guards"])

        if has_server and not has_client:
            result["unmatched"].append({
                "severity": "warning",
                "message": "Server-side auth guards exist but no matching {#if} auth conditionals found in .svelte files",
            })
        if has_client and not has_server:
            result["unmatched"].append({
                "severity": "error",
                "message": "Client-side auth conditionals exist but no server hooks/load auth guard found -- potential security issue",
            })

        result["status"] = "ok"
        return result
