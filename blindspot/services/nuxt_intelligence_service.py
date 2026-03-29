"""
Vue 3 / Nuxt 3 Intelligence Service.

Provides cross-file analysis for Vue 3 and Nuxt 3 projects:
- Vue SFC component tree and dependency mapping
- Nuxt server route parsing (server/api/)
- Composable extraction and consumer mapping
- Component dependency tracing (props, emits, slots, provide/inject)
- Flow mapping (page -> middleware -> layout -> server API)
- State management discovery (Pinia, Vuex, provide/inject)
- Middleware chain resolution
- Plugin discovery
- Auto-import analysis
- Project convention extraction
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class NuxtIntelligenceService(BaseService):
    """Vue 3 / Nuxt 3 code intelligence."""

    # Vue 3 Composition API lifecycle hooks
    VUE_LIFECYCLE_HOOKS = [
        "onMounted", "onUpdated", "onUnmounted", "onBeforeMount",
        "onBeforeUpdate", "onBeforeUnmount", "onActivated", "onDeactivated",
        "onErrorCaptured", "onRenderTracked", "onRenderTriggered",
        "onServerPrefetch",
    ]

    # Nuxt 3 composables
    NUXT_COMPOSABLES = [
        "useAsyncData", "useFetch", "useLazyFetch", "useLazyAsyncData",
        "useHead", "useSeoMeta", "useRoute", "useRouter", "useRuntimeConfig",
        "useState", "useCookie", "useRequestHeaders", "useRequestEvent",
        "useRequestFetch", "useRequestURL", "useAppConfig", "useNuxtApp",
        "useNuxtData", "useError", "useLazyAsyncData", "useHydration",
        "navigateTo", "abortNavigation", "addRouteMiddleware",
        "definePageMeta", "clearError", "createError", "showError",
        "clearNuxtData", "refreshNuxtData", "setPageLayout",
        "setResponseStatus", "prerenderRoutes", "preloadComponents",
        "prefetchComponents", "refreshCookie",
    ]

    # Vue core composables
    VUE_COMPOSABLES = [
        "ref", "reactive", "computed", "watch", "watchEffect",
        "watchPostEffect", "watchSyncEffect", "toRef", "toRefs",
        "toRaw", "unref", "isRef", "isReactive", "isReadonly",
        "shallowRef", "shallowReactive", "shallowReadonly",
        "triggerRef", "customRef", "readonly", "markRaw",
        "effectScope", "getCurrentScope", "onScopeDispose",
        "provide", "inject", "nextTick", "defineComponent",
        "defineAsyncComponent", "h", "mergeProps", "cloneVNode",
    ]

    # Pattern registry for get_similar_patterns
    PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
        "modal": {
            "keywords": ["modal", "dialog", "overlay", "backdrop", "popup"],
            "file_patterns": [r"modal", r"dialog", r"popup", r"overlay"],
            "code_patterns": [
                r"(?:is|show|open)\s*(?:Modal|Dialog|Popup)",
                r"@close|@dismiss|@cancel",
                r"v-if\s*=\s*[\"'](?:is|show)(?:Modal|Dialog|Open)",
                r"<(?:Teleport|teleport)\b",
            ],
        },
        "dropdown": {
            "keywords": ["dropdown", "select", "menu", "popover", "combobox"],
            "file_patterns": [r"dropdown", r"select", r"menu", r"popover", r"combobox"],
            "code_patterns": [
                r"(?:is|show)(?:Open|Dropdown|Menu)",
                r"v-click-outside|clickOutside",
                r"@(?:select|change|toggle)",
                r"<(?:Listbox|Combobox|Menu)\b",
            ],
        },
        "form": {
            "keywords": ["form", "input", "field", "validation", "submit"],
            "file_patterns": [r"form", r"input", r"field"],
            "code_patterns": [
                r"useForm|useField|useVModel",
                r"@submit(?:\.prevent)?",
                r"v-model\b",
                r"<form\b",
                r"(?:vee-validate|vuelidate|zod|yup)",
                r"defineRule|useValidation",
            ],
        },
        "table": {
            "keywords": ["table", "datagrid", "data-table", "grid", "list"],
            "file_patterns": [r"table", r"datagrid", r"data.?table", r"grid"],
            "code_patterns": [
                r"<(?:table|Table|DataTable)\b",
                r"columns?\s*[=:]",
                r"<t(?:head|body|r|d|h)\b",
                r"sortBy|filterBy|pageSize",
                r":items\s*=|:data\s*=|:rows\s*=",
            ],
        },
        "tabs": {
            "keywords": ["tabs", "tab", "tabbed", "tab-panel", "tab-group"],
            "file_patterns": [r"tab", r"tabs"],
            "code_patterns": [
                r"(?:active|current|selected)Tab",
                r"<(?:Tab|TabGroup|TabList|TabPanel|TabPanels)\b",
                r"v-for\s*=.*tab",
                r"@(?:tab-change|select)",
            ],
        },
        "toast": {
            "keywords": ["toast", "notification", "snackbar", "alert", "message"],
            "file_patterns": [r"toast", r"notification", r"snackbar", r"alert"],
            "code_patterns": [
                r"useToast|useNotification",
                r"(?:add|show|push)(?:Toast|Notification|Alert)",
                r"<(?:Toast|Notification|Snackbar)\b",
                r"<Transition",
            ],
        },
        "sidebar": {
            "keywords": ["sidebar", "navigation", "drawer", "aside", "nav"],
            "file_patterns": [r"sidebar", r"drawer", r"aside", r"nav"],
            "code_patterns": [
                r"(?:is|show)(?:Sidebar|Drawer|Nav)(?:Open)?",
                r"<(?:aside|nav)\b",
                r"v-if\s*=\s*[\"'](?:is|show)(?:Sidebar|Drawer)",
                r"<(?:Sidebar|Drawer|Navigation)\b",
            ],
        },
        "carousel": {
            "keywords": ["carousel", "slider", "swiper", "slideshow", "gallery"],
            "file_patterns": [r"carousel", r"slider", r"swiper", r"gallery"],
            "code_patterns": [
                r"(?:current|active)(?:Slide|Index)",
                r"(?:next|prev)Slide",
                r"<(?:Swiper|Carousel|Slider)\b",
                r"swiper|splide|embla",
            ],
        },
        "dialog": {
            "keywords": ["dialog", "confirm", "prompt", "alert-dialog"],
            "file_patterns": [r"dialog", r"confirm", r"prompt"],
            "code_patterns": [
                r"(?:is|show)(?:Dialog|Confirm|Prompt)",
                r"@confirm|@cancel|@close",
                r"<(?:Dialog|AlertDialog|ConfirmDialog)\b",
                r"useDialog|useConfirm",
            ],
        },
        "drawer": {
            "keywords": ["drawer", "slide-over", "panel", "sheet"],
            "file_patterns": [r"drawer", r"slide.?over", r"sheet", r"panel"],
            "code_patterns": [
                r"(?:is|show)(?:Drawer|Panel|Sheet)(?:Open)?",
                r"<(?:Drawer|SlideOver|Sheet)\b",
                r"<Transition.*slide",
                r"translate-x|translateX",
            ],
        },
        "tooltip": {
            "keywords": ["tooltip", "popover", "hint", "info-tip"],
            "file_patterns": [r"tooltip", r"popover", r"hint"],
            "code_patterns": [
                r"v-tooltip|v-tippy",
                r"<(?:Tooltip|Popover)\b",
                r"(?:show|is)(?:Tooltip|Popover)",
                r"@mouseenter|@mouseleave|@hover",
            ],
        },
        "popover": {
            "keywords": ["popover", "floating", "popper", "tippy"],
            "file_patterns": [r"popover", r"floating", r"popper"],
            "code_patterns": [
                r"<(?:Popover|Float)\b",
                r"useFloating|usePopper",
                r"@floating-ui|popperjs|tippy",
                r"(?:placement|strategy|offset)\s*[=:]",
            ],
        },
    }

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
        return None

    def _is_nuxt_project(self, base: str) -> bool:
        """Detect if this is a Nuxt 3 project (vs plain Vue 3)."""
        return os.path.isfile(os.path.join(base, "nuxt.config.ts")) or \
               os.path.isfile(os.path.join(base, "nuxt.config.js"))

    def _read_file(self, fpath: str) -> Optional[str]:
        """Safely read a file, returning None on failure."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def _walk_files(self, directory: str, extensions: Tuple[str, ...] = (".vue", ".ts", ".js", ".tsx", ".jsx")) -> List[str]:
        """Walk a directory and return files matching extensions."""
        results = []
        if not os.path.isdir(directory):
            return results
        for root, _dirs, files in os.walk(directory):
            # Skip node_modules, .nuxt, .output, dist
            rel = os.path.relpath(root, directory)
            skip = False
            for part in rel.split(os.sep):
                if part in ("node_modules", ".nuxt", ".output", "dist", ".git", "__pycache__"):
                    skip = True
                    break
            if skip:
                continue
            for fname in files:
                if fname.endswith(extensions):
                    results.append(os.path.join(root, fname))
        return results

    def _parse_vue_sfc(self, content: str) -> Dict[str, Any]:
        """Parse a .vue SFC into script, template, and style sections."""
        result: Dict[str, Any] = {
            "script_setup": None,
            "script_options": None,
            "template": None,
            "styles": [],
            "is_script_setup": False,
        }

        # Extract <script setup ...> block
        setup_match = re.search(
            r"<script\s+[^>]*setup[^>]*>(.*?)</script>",
            content, re.DOTALL | re.IGNORECASE,
        )
        if setup_match:
            result["script_setup"] = setup_match.group(1)
            result["is_script_setup"] = True

        # Extract <script> (options API / regular)
        options_match = re.search(
            r"<script(?:\s+lang=[\"'](?:ts|js)[\"'])?\s*>(.*?)</script>",
            content, re.DOTALL | re.IGNORECASE,
        )
        if options_match and not setup_match:
            result["script_options"] = options_match.group(1)

        # Extract <template>
        template_match = re.search(
            r"<template>(.*?)</template>",
            content, re.DOTALL | re.IGNORECASE,
        )
        if template_match:
            result["template"] = template_match.group(1)

        # Extract all <style> blocks
        for style_match in re.finditer(
            r"<style([^>]*)>(.*?)</style>",
            content, re.DOTALL | re.IGNORECASE,
        ):
            attrs = style_match.group(1)
            style_info = {
                "scoped": "scoped" in attrs,
                "module": "module" in attrs,
                "lang": "css",
            }
            lang_m = re.search(r'lang=["\'](\w+)["\']', attrs)
            if lang_m:
                style_info["lang"] = lang_m.group(1)
            result["styles"].append(style_info)

        return result

    def _extract_imports(self, script: str) -> List[Dict[str, str]]:
        """Extract import statements from script content."""
        imports = []
        for m in re.finditer(
            r"import\s+(?:(?:type\s+)?(\{[^}]+\})|(\w+)(?:\s*,\s*\{([^}]+)\})?)\s+from\s+['\"]([^'\"]+)['\"]",
            script,
        ):
            named = m.group(1) or m.group(3)
            default = m.group(2)
            source = m.group(4)
            imp: Dict[str, str] = {"source": source}
            if default and default != "type":
                imp["default"] = default
            if named:
                names = [n.strip().split(" as ") for n in named.strip("{}").split(",")]
                imp["named"] = [
                    {"name": parts[0].strip(), "alias": parts[1].strip() if len(parts) > 1 else None}
                    for parts in names if parts[0].strip()
                ]
            imports.append(imp)
        return imports

    def _extract_define_props(self, script: str) -> List[Dict[str, Any]]:
        """Extract defineProps() from script setup."""
        props = []

        # defineProps<{ name: type }>() - TypeScript generic syntax
        ts_match = re.search(
            r"defineProps\s*<\s*\{([^}]+)\}\s*>\s*\(\s*\)",
            script, re.DOTALL,
        )
        if ts_match:
            body = ts_match.group(1)
            for line in body.split("\n"):
                line = line.strip().rstrip(",").rstrip(";")
                if not line or line.startswith("//"):
                    continue
                prop_m = re.match(r"(\w+)\s*(\?)?:\s*(.+)", line)
                if prop_m:
                    props.append({
                        "name": prop_m.group(1),
                        "required": prop_m.group(2) is None,
                        "type": prop_m.group(3).strip(),
                    })
            return props

        # defineProps({ name: { type: String, ... } }) - Object syntax
        obj_match = re.search(
            r"defineProps\s*\(\s*\{(.*?)\}\s*\)",
            script, re.DOTALL,
        )
        if obj_match:
            body = obj_match.group(1)
            # Simple extraction: prop_name: { type: X, required: Y, default: Z }
            for prop_m in re.finditer(
                r"(\w+)\s*:\s*\{([^}]+)\}",
                body, re.DOTALL,
            ):
                name = prop_m.group(1)
                config = prop_m.group(2)
                prop_info: Dict[str, Any] = {"name": name}
                type_m = re.search(r"type\s*:\s*(\w+(?:\s*\|\s*\w+)*)", config)
                if type_m:
                    prop_info["type"] = type_m.group(1).strip()
                req_m = re.search(r"required\s*:\s*(true|false)", config)
                prop_info["required"] = req_m.group(1) == "true" if req_m else False
                def_m = re.search(r"default\s*:\s*(.+?)(?:,|\s*$)", config)
                if def_m:
                    prop_info["default"] = def_m.group(1).strip()
                props.append(prop_info)
            return props

        # defineProps(['name1', 'name2']) - Array syntax
        arr_match = re.search(
            r"defineProps\s*\(\s*\[([^\]]+)\]\s*\)",
            script,
        )
        if arr_match:
            for name_m in re.finditer(r"['\"](\w+)['\"]", arr_match.group(1)):
                props.append({"name": name_m.group(1), "type": "any", "required": False})

        # withDefaults(defineProps<...>(), { ... })
        wd_match = re.search(
            r"withDefaults\s*\(\s*defineProps\s*<\s*\{([^}]+)\}\s*>\s*\(\s*\)\s*,\s*\{([^}]+)\}\s*\)",
            script, re.DOTALL,
        )
        if wd_match and not props:
            body = wd_match.group(1)
            defaults_body = wd_match.group(2)
            defaults = {}
            for dm in re.finditer(r"(\w+)\s*:\s*(.+?)(?:,|\s*$)", defaults_body):
                defaults[dm.group(1).strip()] = dm.group(2).strip()
            for line in body.split("\n"):
                line = line.strip().rstrip(",").rstrip(";")
                if not line or line.startswith("//"):
                    continue
                prop_m = re.match(r"(\w+)\s*(\?)?:\s*(.+)", line)
                if prop_m:
                    name = prop_m.group(1)
                    p = {
                        "name": name,
                        "required": prop_m.group(2) is None and name not in defaults,
                        "type": prop_m.group(3).strip(),
                    }
                    if name in defaults:
                        p["default"] = defaults[name]
                    props.append(p)

        return props

    def _extract_define_emits(self, script: str) -> List[str]:
        """Extract defineEmits() from script setup."""
        emits: List[str] = []

        # defineEmits<{ (e: 'name', payload: T): void }>()
        ts_match = re.search(
            r"defineEmits\s*<\s*\{([^}]+)\}\s*>\s*\(\s*\)",
            script, re.DOTALL,
        )
        if ts_match:
            for em in re.finditer(r"e\s*:\s*['\"](\w[\w-]*)['\"]", ts_match.group(1)):
                emits.append(em.group(1))
            return emits

        # defineEmits(['event1', 'event2'])
        arr_match = re.search(
            r"defineEmits\s*\(\s*\[([^\]]+)\]\s*\)",
            script,
        )
        if arr_match:
            for em in re.finditer(r"['\"](\w[\w-]*)['\"]", arr_match.group(1)):
                emits.append(em.group(1))
            return emits

        # defineEmits<{ name: [payload: T] }>() - record syntax
        rec_match = re.search(
            r"defineEmits\s*<\s*\{([\s\S]*?)\}\s*>\s*\(\s*\)",
            script, re.DOTALL,
        )
        if rec_match:
            for em in re.finditer(r"(\w[\w-]*)\s*:", rec_match.group(1)):
                emits.append(em.group(1))

        return emits

    def _extract_define_expose(self, script: str) -> List[str]:
        """Extract defineExpose() members from script setup."""
        expose_match = re.search(
            r"defineExpose\s*\(\s*\{([^}]*)\}\s*\)",
            script,
        )
        if not expose_match:
            return []
        members = []
        for m in re.finditer(r"(\w+)", expose_match.group(1)):
            members.append(m.group(1))
        return members

    def _extract_composables_used(self, script: str) -> List[Dict[str, str]]:
        """Extract composable calls (useXxx()) from script."""
        composables = []
        seen: Set[str] = set()
        for m in re.finditer(r"\b(use[A-Z]\w*)\s*\(", script):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                category = "nuxt"
                if name in self.VUE_COMPOSABLES or name in self.VUE_LIFECYCLE_HOOKS:
                    category = "vue"
                elif name in self.NUXT_COMPOSABLES:
                    category = "nuxt"
                else:
                    category = "custom"
                composables.append({"name": name, "category": category})
        return composables

    def _extract_template_components(self, template: str) -> List[str]:
        """Extract component names used in a <template> block."""
        components: Set[str] = set()
        # PascalCase: <MyComponent ... /> or <MyComponent ...>
        for m in re.finditer(r"<(/?)([A-Z][A-Za-z0-9]+)", template):
            components.add(m.group(2))
        # kebab-case with prefix: <my-component> (skip html elements)
        for m in re.finditer(r"<(/?)([a-z][\w]*-[\w-]+)", template):
            tag = m.group(2)
            # Convert to PascalCase
            pascal = "".join(word.capitalize() for word in tag.split("-"))
            components.add(pascal)
        # Nuxt built-ins to exclude
        builtins = {
            "Teleport", "Transition", "TransitionGroup", "KeepAlive",
            "Suspense", "Component", "Slot", "NuxtPage", "NuxtLayout",
            "NuxtLink", "NuxtLoadingIndicator", "NuxtErrorBoundary",
            "ClientOnly", "DevOnly", "ServerOnly",
        }
        return sorted(components - builtins)

    def _extract_slots(self, template: str) -> List[Dict[str, Any]]:
        """Extract slot usage from template."""
        slots = []
        # <slot> or <slot name="xxx">
        for m in re.finditer(r"<slot(?:\s+name=[\"'](\w+)[\"'])?\s*/?>", template):
            name = m.group(1) or "default"
            slots.append({"name": name, "type": "provided"})
        # v-slot:name or #name
        for m in re.finditer(r"(?:v-slot|#)(?::(\w+))?\s*(?:=\s*[\"']\{([^}]*)\}[\"'])?", template):
            name = m.group(1) or "default"
            bindings = m.group(2)
            slot_info: Dict[str, Any] = {"name": name, "type": "consumed"}
            if bindings:
                slot_info["bindings"] = [b.strip() for b in bindings.split(",") if b.strip()]
            slots.append(slot_info)
        return slots

    def _to_pascal_case(self, name: str) -> str:
        """Convert kebab-case or snake_case to PascalCase."""
        return "".join(w.capitalize() for w in re.split(r"[-_]", name))

    def _to_kebab_case(self, name: str) -> str:
        """Convert PascalCase to kebab-case."""
        s = re.sub(r"([A-Z])", r"-\1", name).lower().lstrip("-")
        return s

    # ── 1. get_component_tree ────────────────────────────────────────

    def get_component_tree(self, component_name: Optional[str] = None) -> Dict[str, Any]:
        """Parse .vue files to build component tree with props, emits, composables, slots, styles."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        components: List[Dict[str, Any]] = []
        scan_dirs = ["components", "pages", "layouts", "src/components", "src/pages", "src/views"]

        vue_files = []
        for d in scan_dirs:
            full = os.path.join(base, d)
            vue_files.extend(self._walk_files(full, (".vue",)))

        for fpath in vue_files:
            content = self._read_file(fpath)
            if not content:
                continue

            rel = os.path.relpath(fpath, base)
            fname = os.path.splitext(os.path.basename(fpath))[0]
            cname = self._to_pascal_case(fname)

            if component_name and cname.lower() != component_name.lower() and fname.lower() != component_name.lower():
                continue

            sfc = self._parse_vue_sfc(content)
            comp_info: Dict[str, Any] = {
                "name": cname,
                "file": rel,
                "is_script_setup": sfc["is_script_setup"],
            }

            script = sfc["script_setup"] or sfc["script_options"] or ""

            # Props
            props = self._extract_define_props(script)
            if props:
                comp_info["props"] = props

            # Emits
            emits = self._extract_define_emits(script)
            if emits:
                comp_info["emits"] = emits

            # Expose
            expose = self._extract_define_expose(script)
            if expose:
                comp_info["expose"] = expose

            # Composables
            composables = self._extract_composables_used(script)
            if composables:
                comp_info["composables"] = composables

            # Imports
            imports = self._extract_imports(script)
            comp_imports = [
                imp for imp in imports
                if imp.get("source", "").startswith(".")
                or imp.get("source", "").startswith("@/")
                or imp.get("source", "").startswith("~/")
                or imp.get("source", "").startswith("#")
            ]
            if comp_imports:
                comp_info["imports"] = comp_imports

            # Template analysis
            if sfc["template"]:
                child_components = self._extract_template_components(sfc["template"])
                if child_components:
                    comp_info["children"] = child_components

                slots = self._extract_slots(sfc["template"])
                if slots:
                    comp_info["slots"] = slots

            # Styles
            if sfc["styles"]:
                comp_info["styles"] = sfc["styles"]

            components.append(comp_info)

        # Build parent-child index
        child_to_parents: Dict[str, List[str]] = {}
        for comp in components:
            for child in comp.get("children", []):
                child_to_parents.setdefault(child, []).append(comp["name"])

        for comp in components:
            parents = child_to_parents.get(comp["name"], [])
            if parents:
                comp["used_by"] = parents

        result: Dict[str, Any] = {
            "status": "success",
            "project_type": "nuxt3" if is_nuxt else "vue3",
            "total_components": len(components),
            "components": components,
        }
        if component_name and not components:
            result["message"] = f"Component '{component_name}' not found"
        return result

    # ── 2. get_api_routes ────────────────────────────────────────────

    def get_api_routes(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """Parse Nuxt server routes (server/api/) or Vue Router routes."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        routes: List[Dict[str, Any]] = []

        if is_nuxt:
            routes = self._parse_nuxt_server_routes(base, filter_prefix)
        else:
            routes = self._parse_vue_router_routes(base, filter_prefix)

        return {
            "status": "success",
            "project_type": "nuxt3" if is_nuxt else "vue3",
            "total_routes": len(routes),
            "routes": routes,
        }

    def _parse_nuxt_server_routes(self, base: str, filter_prefix: Optional[str]) -> List[Dict[str, Any]]:
        """Parse Nuxt 3 server/api/ routes."""
        routes: List[Dict[str, Any]] = []
        api_dir = os.path.join(base, "server", "api")
        server_dir = os.path.join(base, "server")

        # Also scan server/routes/ for non-API server routes
        scan_dirs = []
        if os.path.isdir(api_dir):
            scan_dirs.append(("api", api_dir))
        routes_dir = os.path.join(server_dir, "routes")
        if os.path.isdir(routes_dir):
            scan_dirs.append(("routes", routes_dir))

        for prefix_label, scan_dir in scan_dirs:
            files = self._walk_files(scan_dir, (".ts", ".js"))
            for fpath in files:
                rel = os.path.relpath(fpath, server_dir)
                content = self._read_file(fpath)
                if not content:
                    continue

                # Derive HTTP method and route path from filename
                fname = os.path.basename(fpath)
                name_no_ext = re.sub(r"\.\w+$", "", fname)

                # Check for method suffix: xxx.get.ts, xxx.post.ts, etc.
                method = "ALL"
                method_match = re.search(r"\.(get|post|put|patch|delete|head|options)$", name_no_ext, re.IGNORECASE)
                if method_match:
                    method = method_match.group(1).upper()
                    name_no_ext = name_no_ext[:method_match.start()]

                # Build route path from directory structure
                route_parts = os.path.relpath(os.path.dirname(fpath), scan_dir).split(os.sep)
                route_parts = [p for p in route_parts if p != "."]
                if name_no_ext != "index":
                    route_parts.append(name_no_ext)

                # Convert [...slug] to :slug, [id] to :id
                normalized = []
                for part in route_parts:
                    part = re.sub(r"\[\.\.\.(\w+)\]", r"**:\1", part)
                    part = re.sub(r"\[(\w+)\]", r":\1", part)
                    normalized.append(part)

                route_path = "/" + "/".join(normalized) if prefix_label == "routes" else "/api/" + "/".join(normalized)

                if filter_prefix and not route_path.startswith(filter_prefix):
                    continue

                route_info: Dict[str, Any] = {
                    "path": route_path,
                    "method": method,
                    "file": rel,
                }

                # Parse handler content
                handler_match = re.search(
                    r"defineEventHandler\s*\(\s*(async\s*)?\(?(?:event)?\)?\s*=>\s*\{?(.*?)(?:\}?\s*\)\s*;?\s*$|\}\s*\))",
                    content, re.DOTALL,
                )
                if handler_match:
                    route_info["async"] = handler_match.group(1) is not None
                    handler_body = handler_match.group(2) or content

                    # Check for body reading
                    if re.search(r"readBody\s*\(", handler_body):
                        route_info["reads_body"] = True
                    if re.search(r"readValidatedBody\s*\(", handler_body):
                        route_info["validated_body"] = True
                    if re.search(r"getQuery\s*\(", handler_body):
                        route_info["reads_query"] = True
                    if re.search(r"getRouterParam\s*\(|getRouterParams\s*\(", handler_body):
                        route_info["reads_params"] = True

                    # Auth checks
                    if re.search(r"requireAuth|isAuthenticated|getServerSession|requireSession", handler_body):
                        route_info["auth_check"] = True

                    # Validation
                    if re.search(r"z\.\w+|zod\.\w+|\.parse\s*\(|\.safeParse\s*\(", handler_body):
                        route_info["has_validation"] = True

                    # Error handling
                    if re.search(r"createError\s*\(", handler_body):
                        route_info["throws_errors"] = True

                else:
                    # Also check for exported default function pattern
                    if re.search(r"export\s+default\s+defineEventHandler", content):
                        route_info["handler"] = "defineEventHandler"

                # Middleware on server route
                mw_match = re.search(r"defineRequestMiddleware|defineResponseMiddleware", content)
                if mw_match:
                    route_info["has_middleware"] = True

                routes.append(route_info)

        return routes

    def _parse_vue_router_routes(self, base: str, filter_prefix: Optional[str]) -> List[Dict[str, Any]]:
        """Parse Vue Router route definitions."""
        routes: List[Dict[str, Any]] = []
        router_files = []

        # Common locations for Vue Router config
        for candidate in [
            "src/router/index.ts", "src/router/index.js",
            "src/router.ts", "src/router.js",
            "router/index.ts", "router/index.js",
        ]:
            fpath = os.path.join(base, candidate)
            if os.path.isfile(fpath):
                router_files.append(fpath)

        for fpath in router_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Extract route objects: { path: '/users', component: UserView, ... }
            for m in re.finditer(
                r"\{\s*path\s*:\s*['\"]([^'\"]+)['\"]"
                r"(?:.*?name\s*:\s*['\"]([^'\"]+)['\"])?"
                r"(?:.*?component\s*:\s*(\w+))?"
                r"(?:.*?meta\s*:\s*\{([^}]*)\})?",
                content, re.DOTALL,
            ):
                path = m.group(1)
                if filter_prefix and not path.startswith(filter_prefix):
                    continue
                route_info: Dict[str, Any] = {"path": path}
                if m.group(2):
                    route_info["name"] = m.group(2)
                if m.group(3):
                    route_info["component"] = m.group(3)
                if m.group(4):
                    # Parse meta fields
                    meta = m.group(4)
                    if re.search(r"requiresAuth\s*:\s*true", meta):
                        route_info["auth_required"] = True
                    route_info["meta"] = meta.strip()

                # Check for beforeEnter guard
                route_block_end = content[m.end():m.end() + 500]
                if re.search(r"beforeEnter\s*:", route_block_end):
                    route_info["has_guard"] = True

                routes.append(route_info)

        return routes

    # ── 3. get_composable_map ────────────────────────────────────────

    def get_composable_map(self, composable_name: Optional[str] = None) -> Dict[str, Any]:
        """Scan composables/ for useXxx() functions and map to consumers."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        composables: List[Dict[str, Any]] = []

        # Scan composables directories
        search_dirs = ["composables", "src/composables", "src/hooks"]
        comp_files = []
        for d in search_dirs:
            full = os.path.join(base, d)
            comp_files.extend(self._walk_files(full, (".ts", ".js", ".vue")))

        for fpath in comp_files:
            content = self._read_file(fpath)
            if not content:
                continue

            rel = os.path.relpath(fpath, base)

            # Find exported useXxx functions
            for m in re.finditer(
                r"(?:export\s+(?:default\s+)?(?:function|const)\s+)(use[A-Z]\w*)",
                content,
            ):
                name = m.group(1)
                if composable_name and name.lower() != composable_name.lower():
                    continue

                comp_info: Dict[str, Any] = {"name": name, "file": rel}

                # Extract reactive state (ref, reactive, shallowRef)
                refs = []
                for ref_m in re.finditer(
                    r"(?:const|let)\s+(\w+)\s*=\s*(ref|reactive|shallowRef|shallowReactive|computed|readonly)\s*[<(]",
                    content,
                ):
                    refs.append({"name": ref_m.group(1), "kind": ref_m.group(2)})
                if refs:
                    comp_info["state"] = refs

                # Extract computed properties
                computeds = []
                for cm in re.finditer(
                    r"(?:const|let)\s+(\w+)\s*=\s*computed\s*\(",
                    content,
                ):
                    computeds.append(cm.group(1))
                if computeds:
                    comp_info["computed"] = computeds

                # Extract watchers
                watchers = []
                for wm in re.finditer(
                    r"(watch|watchEffect|watchPostEffect|watchSyncEffect)\s*\(\s*(?:(?:\[([^\]]+)\])|(\w+))?",
                    content,
                ):
                    watch_info: Dict[str, Any] = {"type": wm.group(1)}
                    if wm.group(2):
                        watch_info["sources"] = [s.strip() for s in wm.group(2).split(",")]
                    elif wm.group(3):
                        watch_info["sources"] = [wm.group(3)]
                    watchers.append(watch_info)
                if watchers:
                    comp_info["watchers"] = watchers

                # Extract lifecycle hooks used
                hooks = []
                for hook in self.VUE_LIFECYCLE_HOOKS:
                    if re.search(rf"\b{re.escape(hook)}\s*\(", content):
                        hooks.append(hook)
                if hooks:
                    comp_info["lifecycle_hooks"] = hooks

                # Extract return values
                return_match = re.search(
                    r"return\s*\{([^}]+)\}",
                    content,
                )
                if return_match:
                    returns = [
                        r.strip().split(":")[0].strip()
                        for r in return_match.group(1).split(",")
                        if r.strip()
                    ]
                    comp_info["returns"] = returns

                # Find consumers of this composable across the project
                consumers = self._find_composable_consumers(base, name)
                if consumers:
                    comp_info["consumers"] = consumers

                composables.append(comp_info)

        result: Dict[str, Any] = {
            "status": "success",
            "total_composables": len(composables),
            "composables": composables,
        }
        if composable_name and not composables:
            result["message"] = f"Composable '{composable_name}' not found"
        return result

    def _find_composable_consumers(self, base: str, composable_name: str) -> List[Dict[str, str]]:
        """Find all .vue and .ts files that call a specific composable."""
        consumers = []
        all_files = self._walk_files(base, (".vue", ".ts", ".js"))
        pattern = re.compile(rf"\b{re.escape(composable_name)}\s*\(")

        for fpath in all_files:
            # Skip node_modules and composables definitions dir
            rel = os.path.relpath(fpath, base)
            if "node_modules" in rel or ".nuxt" in rel:
                continue

            content = self._read_file(fpath)
            if not content:
                continue

            if pattern.search(content):
                # Don't include the definition file itself (unless it re-uses)
                if f"export" in content and f"function {composable_name}" in content:
                    continue
                if f"export" in content and f"const {composable_name}" in content:
                    continue
                consumers.append({"file": rel})

        return consumers[:20]  # Cap to save context

    # ── 4. get_component_dependencies ────────────────────────────────

    def get_component_dependencies(self, component_path: str) -> Dict[str, Any]:
        """Trace all dependencies for a .vue component file."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        fpath = os.path.join(base, component_path)
        if not os.path.isfile(fpath):
            return {"status": "error", "message": f"File not found: {component_path}"}

        content = self._read_file(fpath)
        if not content:
            return {"status": "error", "message": f"Could not read: {component_path}"}

        sfc = self._parse_vue_sfc(content)
        script = sfc["script_setup"] or sfc["script_options"] or ""
        fname = os.path.splitext(os.path.basename(fpath))[0]
        cname = self._to_pascal_case(fname)

        result: Dict[str, Any] = {
            "status": "success",
            "component": cname,
            "file": component_path,
        }

        # Props
        props = self._extract_define_props(script)
        if props:
            result["props"] = props

        # Emits
        emits = self._extract_define_emits(script)
        if emits:
            result["emits"] = emits

        # Expose
        expose = self._extract_define_expose(script)
        if expose:
            result["expose"] = expose

        # Child components in template
        if sfc["template"]:
            children = self._extract_template_components(sfc["template"])
            if children:
                result["children"] = children

            slots = self._extract_slots(sfc["template"])
            if slots:
                result["slots"] = slots

        # Composables injected
        composables = self._extract_composables_used(script)
        if composables:
            result["composables"] = composables

        # Imports
        imports = self._extract_imports(script)
        if imports:
            result["imports"] = imports

        # provide/inject usage
        provide_matches = re.findall(r"provide\s*\(\s*['\"](\w+)['\"]", script)
        if provide_matches:
            result["provides"] = provide_matches
        inject_matches = re.findall(r"inject\s*\(\s*['\"](\w+)['\"]", script)
        if inject_matches:
            result["injects"] = inject_matches

        # Find parent components that import this one
        parents = self._find_component_parents(base, cname, component_path)
        if parents:
            result["used_by"] = parents

        return result

    def _find_component_parents(self, base: str, component_name: str, component_path: str) -> List[Dict[str, str]]:
        """Find all components that use the given component."""
        parents = []
        kebab = self._to_kebab_case(component_name)
        vue_files = self._walk_files(base, (".vue",))

        for fpath in vue_files:
            rel = os.path.relpath(fpath, base)
            if rel == component_path:
                continue

            content = self._read_file(fpath)
            if not content:
                continue

            # Check template for component usage
            template_match = re.search(r"<template>(.*?)</template>", content, re.DOTALL)
            if template_match:
                tmpl = template_match.group(1)
                if re.search(rf"</?{re.escape(component_name)}\b", tmpl) or \
                   re.search(rf"</?{re.escape(kebab)}\b", tmpl):
                    parents.append({"file": rel})
                    continue

            # Check script for import
            if re.search(rf"import\s+.*{re.escape(component_name)}", content):
                parents.append({"file": rel})

        return parents[:20]

    # ── 5. get_flow_map ──────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """Trace request flow from entry point through middleware, layout, API, DB."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        flow: Dict[str, Any] = {
            "status": "success",
            "entry_point": entry_point,
            "steps": [],
        }

        if is_nuxt:
            self._trace_nuxt_flow(base, entry_point, method, flow)
        else:
            self._trace_vue_flow(base, entry_point, method, flow)

        return flow

    def _trace_nuxt_flow(self, base: str, entry_point: str, method: Optional[str], flow: Dict[str, Any]) -> None:
        """Trace Nuxt 3 request flow."""
        # Determine if entry is a page or API route
        is_api = entry_point.startswith("/api/") or entry_point.startswith("server/")

        if is_api:
            self._trace_nuxt_api_flow(base, entry_point, method, flow)
        else:
            self._trace_nuxt_page_flow(base, entry_point, flow)

    def _trace_nuxt_api_flow(self, base: str, entry_point: str, method: Optional[str], flow: Dict[str, Any]) -> None:
        """Trace Nuxt server API flow."""
        # Find the server route file
        if entry_point.startswith("/api/"):
            route_path = entry_point[5:]  # Remove /api/ prefix
            api_dir = os.path.join(base, "server", "api")
        elif entry_point.startswith("server/"):
            route_path = entry_point
            api_dir = base
        else:
            route_path = entry_point
            api_dir = os.path.join(base, "server", "api")

        # Try to find the handler file
        candidates = []
        method_lower = method.lower() if method else None
        parts = route_path.strip("/").split("/")

        # Build candidate file paths
        base_path = os.path.join(api_dir, *parts) if not entry_point.startswith("server/") else os.path.join(base, entry_point)
        for ext in [".ts", ".js"]:
            if method_lower:
                candidates.append(f"{base_path}.{method_lower}{ext}")
            candidates.append(f"{base_path}{ext}")
            candidates.append(os.path.join(base_path, f"index{ext}"))
            if method_lower:
                candidates.append(os.path.join(base_path, f"index.{method_lower}{ext}"))

        handler_file = None
        for c in candidates:
            if os.path.isfile(c):
                handler_file = c
                break

        if not handler_file:
            flow["steps"].append({"step": "route_resolution", "status": "not_found", "tried": [os.path.relpath(c, base) for c in candidates[:4]]})
            return

        rel = os.path.relpath(handler_file, base)
        flow["steps"].append({"step": "route_resolution", "file": rel})

        content = self._read_file(handler_file) or ""

        # Check for server middleware
        mw_dir = os.path.join(base, "server", "middleware")
        if os.path.isdir(mw_dir):
            mw_files = self._walk_files(mw_dir, (".ts", ".js"))
            middlewares = []
            for mf in mw_files:
                mw_content = self._read_file(mf)
                if mw_content and re.search(r"defineEventHandler|export\s+default", mw_content):
                    middlewares.append(os.path.relpath(mf, base))
            if middlewares:
                flow["steps"].append({"step": "server_middleware", "files": middlewares})

        # Parse handler
        handler_info: Dict[str, Any] = {"step": "handler", "file": rel}
        if re.search(r"readBody\s*\(", content):
            handler_info["reads_body"] = True
        if re.search(r"getQuery\s*\(", content):
            handler_info["reads_query"] = True
        if re.search(r"getRouterParam", content):
            handler_info["reads_params"] = True

        # Validation step
        validation_patterns = []
        if re.search(r"z\.\w+|\.parse\s*\(|\.safeParse\s*\(", content):
            validation_patterns.append("zod")
        if re.search(r"readValidatedBody\s*\(", content):
            validation_patterns.append("nuxt-validated-body")
        if validation_patterns:
            flow["steps"].append({"step": "validation", "frameworks": validation_patterns})

        flow["steps"].append(handler_info)

        # Database/ORM calls
        db_calls = []
        if re.search(r"prisma\.\w+", content):
            db_calls.append("prisma")
            for dm in re.finditer(r"prisma\.(\w+)\.(\w+)\s*\(", content):
                db_calls.append(f"prisma.{dm.group(1)}.{dm.group(2)}")
        if re.search(r"drizzle|db\.\w+\.(?:select|insert|update|delete)", content):
            db_calls.append("drizzle")
        if re.search(r"knex|\.table\s*\(|\.from\s*\(", content):
            db_calls.append("knex")
        if db_calls:
            flow["steps"].append({"step": "database", "operations": db_calls})

        # External service calls
        ext_calls = []
        if re.search(r"\$fetch\s*\(|fetch\s*\(|ofetch\s*\(", content):
            for fm in re.finditer(r"(?:\$fetch|fetch|ofetch)\s*\(\s*['\"]([^'\"]+)['\"]", content):
                ext_calls.append(fm.group(1))
        if ext_calls:
            flow["steps"].append({"step": "external_calls", "urls": ext_calls})

        # Response
        if re.search(r"createError\s*\(", content):
            flow["steps"].append({"step": "error_handling", "uses_createError": True})

    def _trace_nuxt_page_flow(self, base: str, entry_point: str, flow: Dict[str, Any]) -> None:
        """Trace Nuxt page flow: page -> middleware -> layout -> composables -> API."""
        # Find the page file
        page_file = None
        parts = entry_point.strip("/").split("/")
        pages_dir = os.path.join(base, "pages")

        if entry_point.endswith(".vue"):
            page_file = os.path.join(base, entry_point)
        else:
            # Try to resolve URL path to page file
            for ext in [".vue"]:
                candidate = os.path.join(pages_dir, *parts) + ext
                if os.path.isfile(candidate):
                    page_file = candidate
                    break
                candidate = os.path.join(pages_dir, *parts, f"index{ext}")
                if os.path.isfile(candidate):
                    page_file = candidate
                    break

        if not page_file or not os.path.isfile(page_file):
            flow["steps"].append({"step": "page_resolution", "status": "not_found", "entry": entry_point})
            return

        content = self._read_file(page_file) or ""
        rel = os.path.relpath(page_file, base)
        flow["steps"].append({"step": "page", "file": rel})

        # Check definePageMeta for middleware and layout
        meta_match = re.search(
            r"definePageMeta\s*\(\s*\{([^}]+)\}\s*\)",
            content, re.DOTALL,
        )
        if meta_match:
            meta_body = meta_match.group(1)
            # Middleware
            mw_match = re.search(r"middleware\s*:\s*(?:\[([^\]]+)\]|['\"](\w+)['\"])", meta_body)
            if mw_match:
                mw_list = mw_match.group(1) or mw_match.group(2)
                middlewares = [m.strip().strip("'\"") for m in mw_list.split(",")]
                flow["steps"].append({"step": "middleware", "names": middlewares})

                # Resolve middleware files
                for mw_name in middlewares:
                    for ext in [".ts", ".js"]:
                        mw_file = os.path.join(base, "middleware", f"{mw_name}{ext}")
                        if os.path.isfile(mw_file):
                            flow["steps"][-1].setdefault("files", []).append(
                                os.path.relpath(mw_file, base)
                            )

            # Layout
            layout_match = re.search(r"layout\s*:\s*['\"](\w+)['\"]", meta_body)
            if layout_match:
                layout_name = layout_match.group(1)
                flow["steps"].append({"step": "layout", "name": layout_name})
                for ext in [".vue"]:
                    layout_file = os.path.join(base, "layouts", f"{layout_name}{ext}")
                    if os.path.isfile(layout_file):
                        flow["steps"][-1]["file"] = os.path.relpath(layout_file, base)

        # Composables and API calls in the page
        sfc = self._parse_vue_sfc(content)
        script = sfc["script_setup"] or sfc["script_options"] or ""

        composables = self._extract_composables_used(script)
        if composables:
            flow["steps"].append({"step": "composables", "used": composables})

        # useFetch / useAsyncData calls (server API calls from page)
        fetch_calls = []
        for fm in re.finditer(r"(?:useFetch|useAsyncData|useLazyFetch|useLazyAsyncData|\$fetch)\s*\(\s*['\"]([^'\"]+)['\"]", script):
            fetch_calls.append({"composable": "useFetch", "url": fm.group(1)})
        if fetch_calls:
            flow["steps"].append({"step": "data_fetching", "calls": fetch_calls})

    def _trace_vue_flow(self, base: str, entry_point: str, method: Optional[str], flow: Dict[str, Any]) -> None:
        """Trace Vue 3 (non-Nuxt) flow: route -> guard -> component -> store -> API."""
        # Find the component/page
        page_file = None
        if entry_point.endswith(".vue"):
            page_file = os.path.join(base, entry_point)
        else:
            # Try src/views or src/pages
            for d in ["src/views", "src/pages", "views", "pages"]:
                for ext in [".vue"]:
                    candidate = os.path.join(base, d, entry_point.strip("/")) + ext
                    if os.path.isfile(candidate):
                        page_file = candidate
                        break

        if page_file and os.path.isfile(page_file):
            rel = os.path.relpath(page_file, base)
            flow["steps"].append({"step": "component", "file": rel})

            content = self._read_file(page_file) or ""
            sfc = self._parse_vue_sfc(content)
            script = sfc["script_setup"] or sfc["script_options"] or ""

            # Check for store usage
            store_calls = re.findall(r"\b(use\w+Store)\s*\(", script)
            if store_calls:
                flow["steps"].append({"step": "store", "stores": list(set(store_calls))})

            # Check for API calls
            api_calls = []
            for am in re.finditer(r"(?:axios|fetch|\$http|api)\s*\.?\s*(?:get|post|put|patch|delete)?\s*\(\s*['\"`]([^'\"`]+)['\"`]", script):
                api_calls.append(am.group(1))
            if api_calls:
                flow["steps"].append({"step": "api_calls", "urls": list(set(api_calls))})
        else:
            flow["steps"].append({"step": "component_resolution", "status": "not_found", "entry": entry_point})

        # Check router for guards
        for router_file in ["src/router/index.ts", "src/router/index.js"]:
            rfpath = os.path.join(base, router_file)
            if os.path.isfile(rfpath):
                rcontent = self._read_file(rfpath) or ""
                if re.search(r"beforeEach|beforeResolve", rcontent):
                    flow["steps"].insert(0, {"step": "router_guard", "file": router_file})
                break

    # ── 6. get_state_management ──────────────────────────────────────

    def get_state_management(self) -> Dict[str, Any]:
        """Detect Pinia stores, Vuex stores, and provide/inject patterns."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "pinia_stores": [],
            "vuex_stores": [],
            "provide_inject": [],
        }

        # Scan for Pinia stores
        store_dirs = ["stores", "src/stores", "store", "src/store"]
        store_files = []
        for d in store_dirs:
            full = os.path.join(base, d)
            store_files.extend(self._walk_files(full, (".ts", ".js")))

        # Also search all files for defineStore
        all_ts_files = self._walk_files(base, (".ts", ".js"))

        pinia_seen: Set[str] = set()

        for fpath in store_files + all_ts_files:
            content = self._read_file(fpath)
            if not content:
                continue
            rel = os.path.relpath(fpath, base)

            # Pinia: defineStore('name', { ... }) or defineStore('name', () => { ... })
            for m in re.finditer(
                r"defineStore\s*\(\s*['\"](\w[\w-]*)['\"]",
                content,
            ):
                store_name = m.group(1)
                if store_name in pinia_seen:
                    continue
                pinia_seen.add(store_name)

                store_info: Dict[str, Any] = {"name": store_name, "file": rel}

                # Setup store (composition API) vs Options store
                # Check if second arg is a function
                after_name = content[m.end():]
                if re.match(r"\s*,\s*\(\s*\)\s*=>", after_name):
                    store_info["style"] = "setup"
                else:
                    store_info["style"] = "options"

                # Extract state fields
                state_fields = []
                if store_info["style"] == "setup":
                    # Setup store: const name = ref(...), const name = reactive(...)
                    for sf in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*(ref|reactive|shallowRef)\s*[<(]", content[m.start():]):
                        state_fields.append({"name": sf.group(1), "kind": sf.group(2)})
                else:
                    # Options store: state: () => ({ field: value })
                    state_match = re.search(r"state\s*:\s*\(\s*\)\s*=>\s*\(\s*\{([^}]+)\}", content[m.start():])
                    if state_match:
                        for sf in re.finditer(r"(\w+)\s*:", state_match.group(1)):
                            state_fields.append({"name": sf.group(1)})
                if state_fields:
                    store_info["state"] = state_fields[:15]

                # Extract actions
                actions = []
                if store_info["style"] == "options":
                    actions_match = re.search(r"actions\s*:\s*\{(.*?)\n\s*\}", content[m.start():], re.DOTALL)
                    if actions_match:
                        for am in re.finditer(r"(?:async\s+)?(\w+)\s*\(", actions_match.group(1)):
                            actions.append(am.group(1))
                else:
                    # Setup: exported functions
                    for am in re.finditer(r"(?:const|function)\s+(\w+)\s*=?\s*(?:async\s*)?\(", content[m.start():]):
                        name = am.group(1)
                        if not name.startswith("_") and name not in [s["name"] for s in state_fields]:
                            actions.append(name)
                if actions:
                    store_info["actions"] = actions[:15]

                # Extract getters
                getters = []
                if store_info["style"] == "options":
                    getters_match = re.search(r"getters\s*:\s*\{(.*?)\n\s*\}", content[m.start():], re.DOTALL)
                    if getters_match:
                        for gm in re.finditer(r"(\w+)\s*\(", getters_match.group(1)):
                            getters.append(gm.group(1))
                else:
                    for gm in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*computed\s*\(", content[m.start():]):
                        getters.append(gm.group(1))
                if getters:
                    store_info["getters"] = getters[:15]

                # Find consumers
                store_use_name = f"use{store_name[0].upper()}{store_name[1:]}Store" if not store_name.startswith("use") else store_name
                # Also check for the common pattern useXxxStore
                pascal_name = self._to_pascal_case(store_name)
                possible_hooks = [
                    f"use{pascal_name}Store",
                    store_use_name,
                ]
                consumers = []
                for tf in all_ts_files:
                    if tf == fpath:
                        continue
                    tc = self._read_file(tf)
                    if not tc:
                        continue
                    for hook_name in possible_hooks:
                        if hook_name in tc:
                            consumers.append({"file": os.path.relpath(tf, base)})
                            break
                if consumers:
                    store_info["consumers"] = consumers[:10]

                result["pinia_stores"].append(store_info)

            # Vuex: createStore({ ... })
            if "createStore" in content and fpath not in [sf for sf in store_files]:
                for vm in re.finditer(r"createStore\s*\(\s*\{", content):
                    vuex_info: Dict[str, Any] = {"file": rel}
                    # Extract modules
                    mod_match = re.search(r"modules\s*:\s*\{([^}]+)\}", content[vm.start():])
                    if mod_match:
                        modules = re.findall(r"(\w+)\s*[,:]", mod_match.group(1))
                        vuex_info["modules"] = modules
                    result["vuex_stores"].append(vuex_info)

        # Scan for provide/inject patterns
        vue_files = self._walk_files(base, (".vue", ".ts", ".js"))
        provide_keys: Dict[str, Dict[str, Any]] = {}

        for fpath in vue_files:
            content = self._read_file(fpath)
            if not content:
                continue
            rel = os.path.relpath(fpath, base)

            for pm in re.finditer(r"provide\s*\(\s*['\"](\w+)['\"]", content):
                key = pm.group(1)
                if key not in provide_keys:
                    provide_keys[key] = {"key": key, "providers": [], "injectors": []}
                provide_keys[key]["providers"].append(rel)

            for im in re.finditer(r"inject\s*\(\s*['\"](\w+)['\"]", content):
                key = im.group(1)
                if key not in provide_keys:
                    provide_keys[key] = {"key": key, "providers": [], "injectors": []}
                provide_keys[key]["injectors"].append(rel)

        result["provide_inject"] = list(provide_keys.values())

        return result

    # ── 7. get_middleware_chain ───────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """Parse middleware directory and route middleware configuration."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        result: Dict[str, Any] = {
            "status": "success",
            "project_type": "nuxt3" if is_nuxt else "vue3",
            "middlewares": [],
            "global_middlewares": [],
        }

        if is_nuxt:
            self._parse_nuxt_middleware(base, route_path, result)
        else:
            self._parse_vue_router_guards(base, route_path, result)

        return result

    def _parse_nuxt_middleware(self, base: str, route_path: Optional[str], result: Dict[str, Any]) -> None:
        """Parse Nuxt 3 middleware directory."""
        mw_dir = os.path.join(base, "middleware")
        if not os.path.isdir(mw_dir):
            result["message"] = "No middleware/ directory found"
            return

        mw_files = self._walk_files(mw_dir, (".ts", ".js"))
        for fpath in mw_files:
            content = self._read_file(fpath)
            if not content:
                continue

            rel = os.path.relpath(fpath, base)
            fname = os.path.splitext(os.path.basename(fpath))[0]

            mw_info: Dict[str, Any] = {"name": fname, "file": rel}

            # Global middleware: xxx.global.ts
            if ".global." in os.path.basename(fpath):
                mw_info["global"] = True
                result["global_middlewares"].append(fname)

            # Parse middleware body
            if re.search(r"defineNuxtRouteMiddleware", content):
                mw_info["type"] = "defineNuxtRouteMiddleware"
            elif re.search(r"export\s+default\s+defineNuxtRouteMiddleware", content):
                mw_info["type"] = "defineNuxtRouteMiddleware"
            else:
                mw_info["type"] = "default_export"

            # Check for navigateTo / abortNavigation
            if re.search(r"navigateTo\s*\(", content):
                mw_info["redirects"] = True
                redirect_targets = []
                for rm in re.finditer(r"navigateTo\s*\(\s*['\"]([^'\"]+)['\"]", content):
                    redirect_targets.append(rm.group(1))
                if redirect_targets:
                    mw_info["redirect_targets"] = redirect_targets

            if re.search(r"abortNavigation\s*\(", content):
                mw_info["can_abort"] = True

            # Auth checks
            if re.search(r"useAuth|isAuthenticated|useSession|useState.*auth", content):
                mw_info["auth_check"] = True

            result["middlewares"].append(mw_info)

        # If route_path provided, find which middlewares apply
        if route_path:
            applicable = list(result["global_middlewares"])  # Globals always apply

            # Check pages that match route_path for their middleware
            pages_dir = os.path.join(base, "pages")
            if os.path.isdir(pages_dir):
                for fpath in self._walk_files(pages_dir, (".vue",)):
                    content = self._read_file(fpath) or ""
                    meta = re.search(r"definePageMeta\s*\(\s*\{([^}]+)\}\s*\)", content, re.DOTALL)
                    if meta:
                        mw_match = re.search(r"middleware\s*:\s*(?:\[([^\]]+)\]|['\"](\w+)['\"])", meta.group(1))
                        if mw_match:
                            mw_list = mw_match.group(1) or mw_match.group(2)
                            names = [n.strip().strip("'\"") for n in mw_list.split(",")]
                            applicable.extend(names)

            result["applicable_to_route"] = {
                "route": route_path,
                "middlewares": list(set(applicable)),
            }

    def _parse_vue_router_guards(self, base: str, route_path: Optional[str], result: Dict[str, Any]) -> None:
        """Parse Vue Router navigation guards."""
        for router_file in ["src/router/index.ts", "src/router/index.js", "router/index.ts", "router/index.js"]:
            rfpath = os.path.join(base, router_file)
            if not os.path.isfile(rfpath):
                continue

            content = self._read_file(rfpath) or ""

            # Global guards
            for guard_type in ["beforeEach", "beforeResolve", "afterEach"]:
                if re.search(rf"router\.{guard_type}\s*\(", content):
                    guard_info: Dict[str, Any] = {
                        "name": guard_type,
                        "type": "global",
                        "file": router_file,
                    }
                    # Check for auth logic
                    guard_block = content[content.index(guard_type):]
                    if re.search(r"isAuthenticated|requiresAuth|auth|token|session", guard_block[:500]):
                        guard_info["auth_check"] = True
                    if re.search(r"next\s*\(\s*['\"/]", guard_block[:500]) or re.search(r"navigateTo|redirect", guard_block[:500]):
                        guard_info["redirects"] = True
                    result["middlewares"].append(guard_info)
                    result["global_middlewares"].append(guard_type)

            # Per-route guards (beforeEnter)
            for m in re.finditer(
                r"path\s*:\s*['\"]([^'\"]+)['\"].*?beforeEnter\s*:",
                content, re.DOTALL,
            ):
                route = m.group(1)
                result["middlewares"].append({
                    "name": f"beforeEnter:{route}",
                    "type": "per_route",
                    "route": route,
                    "file": router_file,
                })

            break

    # ── 8. get_plugin_map ────────────────────────────────────────────

    def get_plugin_map(self) -> Dict[str, Any]:
        """Discover Nuxt plugins or Vue app.use() plugin registrations."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        plugins: List[Dict[str, Any]] = []

        if is_nuxt:
            plugins_dir = os.path.join(base, "plugins")
            if os.path.isdir(plugins_dir):
                for fpath in self._walk_files(plugins_dir, (".ts", ".js")):
                    content = self._read_file(fpath)
                    if not content:
                        continue
                    rel = os.path.relpath(fpath, base)
                    fname = os.path.basename(fpath)

                    plugin_info: Dict[str, Any] = {"file": rel, "name": os.path.splitext(fname)[0]}

                    # Client/server only
                    if ".client." in fname:
                        plugin_info["mode"] = "client"
                    elif ".server." in fname:
                        plugin_info["mode"] = "server"
                    else:
                        plugin_info["mode"] = "universal"

                    # defineNuxtPlugin
                    if re.search(r"defineNuxtPlugin", content):
                        plugin_info["type"] = "defineNuxtPlugin"

                    # Check what the plugin provides
                    provides = []
                    for pm in re.finditer(r"provide\s*\(\s*['\"](\w+)['\"]", content):
                        provides.append(pm.group(1))
                    # Also nuxtApp.provide pattern
                    for pm in re.finditer(r"nuxtApp\.provide\s*\(\s*['\"](\w+)['\"]", content):
                        provides.append(pm.group(1))
                    # Also return { provide: { key: value } }
                    prov_match = re.search(r"provide\s*:\s*\{([^}]+)\}", content)
                    if prov_match:
                        for pm in re.finditer(r"(\w+)\s*[,:]", prov_match.group(1)):
                            provides.append(pm.group(1))
                    if provides:
                        plugin_info["provides"] = list(set(provides))

                    # Check for external library usage
                    imports = self._extract_imports(content)
                    ext_deps = [imp["source"] for imp in imports if not imp["source"].startswith(".") and not imp["source"].startswith("#")]
                    if ext_deps:
                        plugin_info["dependencies"] = ext_deps

                    plugins.append(plugin_info)
        else:
            # Vue 3: scan main.ts/main.js for app.use()
            for main_file in ["src/main.ts", "src/main.js", "main.ts", "main.js"]:
                fpath = os.path.join(base, main_file)
                if not os.path.isfile(fpath):
                    continue

                content = self._read_file(fpath) or ""

                for m in re.finditer(r"app\.use\s*\(\s*(\w+)", content):
                    plugin_name = m.group(1)
                    plugin_info = {"name": plugin_name, "file": main_file}

                    # Try to find the import source
                    imp_match = re.search(rf"import\s+.*{re.escape(plugin_name)}.*from\s+['\"]([^'\"]+)['\"]", content)
                    if imp_match:
                        plugin_info["source"] = imp_match.group(1)

                    plugins.append(plugin_info)
                break

        return {
            "status": "success",
            "project_type": "nuxt3" if is_nuxt else "vue3",
            "total_plugins": len(plugins),
            "plugins": plugins,
        }

    # ── 9. verify_endpoint ───────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """Verify that a server route exists and is properly configured."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        result: Dict[str, Any] = {
            "status": "success",
            "method": method.upper(),
            "url": url,
            "checks": [],
        }

        if not is_nuxt:
            result["checks"].append({"check": "nuxt_detection", "status": "skip", "message": "Not a Nuxt project — server routes only apply to Nuxt"})
            return result

        # 1. Route file exists
        route_path = url.lstrip("/")
        if route_path.startswith("api/"):
            route_path = route_path[4:]  # Remove api/ prefix
            search_base = os.path.join(base, "server", "api")
        else:
            search_base = os.path.join(base, "server", "routes")

        parts = route_path.strip("/").split("/")
        method_lower = method.lower()

        candidates = []
        base_path = os.path.join(search_base, *parts)
        for ext in [".ts", ".js"]:
            candidates.append(f"{base_path}.{method_lower}{ext}")
            candidates.append(f"{base_path}{ext}")
            candidates.append(os.path.join(base_path, f"index.{method_lower}{ext}"))
            candidates.append(os.path.join(base_path, f"index{ext}"))

        handler_file = None
        for c in candidates:
            if os.path.isfile(c):
                handler_file = c
                break

        if handler_file:
            rel = os.path.relpath(handler_file, base)
            result["checks"].append({"check": "route_file", "status": "pass", "file": rel})
        else:
            result["checks"].append({
                "check": "route_file",
                "status": "fail",
                "message": f"No handler file found for {method.upper()} {url}",
                "tried": [os.path.relpath(c, base) for c in candidates[:4]],
            })
            return result

        # 2. Handler defined
        content = self._read_file(handler_file) or ""
        if re.search(r"defineEventHandler\s*\(", content):
            result["checks"].append({"check": "handler_defined", "status": "pass"})
        else:
            result["checks"].append({"check": "handler_defined", "status": "fail", "message": "No defineEventHandler() found"})

        # 3. Method match
        fname = os.path.basename(handler_file)
        if f".{method_lower}." in fname:
            result["checks"].append({"check": "method_match", "status": "pass", "explicit_method": True})
        else:
            result["checks"].append({"check": "method_match", "status": "warn", "message": f"File handles all methods (no .{method_lower}. suffix)"})

        # 4. Auth check
        if re.search(r"requireAuth|isAuthenticated|getServerSession|useAuth|requireSession", content):
            result["checks"].append({"check": "auth", "status": "pass"})
        else:
            result["checks"].append({"check": "auth", "status": "info", "message": "No auth check detected"})

        # 5. Input validation
        if re.search(r"z\.\w+|\.parse\s*\(|\.safeParse\s*\(|readValidatedBody", content):
            result["checks"].append({"check": "validation", "status": "pass"})
        elif method.upper() in ("POST", "PUT", "PATCH"):
            result["checks"].append({"check": "validation", "status": "warn", "message": "No input validation for write endpoint"})

        # 6. Error handling
        if re.search(r"createError\s*\(|try\s*\{|catch\s*\(", content):
            result["checks"].append({"check": "error_handling", "status": "pass"})
        else:
            result["checks"].append({"check": "error_handling", "status": "warn", "message": "No explicit error handling"})

        # 7. Server middleware
        mw_dir = os.path.join(base, "server", "middleware")
        if os.path.isdir(mw_dir):
            mw_files = [os.path.relpath(f, base) for f in self._walk_files(mw_dir, (".ts", ".js"))]
            if mw_files:
                result["checks"].append({"check": "server_middleware", "status": "info", "files": mw_files})

        return result

    # ── 10. get_project_conventions ──────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """Extract real conventions from the project codebase."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        handlers = {
            "naming": self._conventions_naming,
            "state": self._conventions_state,
            "styling": self._conventions_styling,
            "testing": self._conventions_testing,
            "auto-imports": self._conventions_auto_imports,
        }

        handler = handlers.get(pattern_type)
        if not handler:
            return {
                "status": "error",
                "message": f"Unknown pattern type: {pattern_type}. Valid: {', '.join(handlers.keys())}",
            }

        result = handler(base)
        result["status"] = "success"
        result["pattern_type"] = pattern_type
        return result

    def _conventions_naming(self, base: str) -> Dict[str, Any]:
        """Analyze naming conventions for components, composables, files."""
        result: Dict[str, Any] = {"conventions": []}

        # Component naming
        comp_dir = os.path.join(base, "components")
        if not os.path.isdir(comp_dir):
            comp_dir = os.path.join(base, "src", "components")

        if os.path.isdir(comp_dir):
            vue_files = self._walk_files(comp_dir, (".vue",))
            pascal_count = 0
            kebab_count = 0
            for fpath in vue_files:
                fname = os.path.splitext(os.path.basename(fpath))[0]
                if re.match(r"^[A-Z][a-zA-Z0-9]+$", fname):
                    pascal_count += 1
                elif re.match(r"^[a-z][\w-]+$", fname):
                    kebab_count += 1

            if pascal_count > kebab_count:
                result["conventions"].append({"type": "component_naming", "pattern": "PascalCase", "count": pascal_count})
            elif kebab_count > 0:
                result["conventions"].append({"type": "component_naming", "pattern": "kebab-case", "count": kebab_count})

            # Check for directory-based naming (e.g., Button/index.vue vs Button.vue)
            index_count = sum(1 for f in vue_files if os.path.basename(f) == "index.vue")
            if index_count > 0:
                result["conventions"].append({"type": "component_structure", "pattern": "directory_with_index", "count": index_count})

        # Composable naming
        comp_dirs = ["composables", "src/composables"]
        for d in comp_dirs:
            full = os.path.join(base, d)
            if os.path.isdir(full):
                files = self._walk_files(full, (".ts", ".js"))
                use_prefix_count = sum(1 for f in files if os.path.basename(f).startswith("use"))
                result["conventions"].append({"type": "composable_naming", "pattern": "useXxx", "count": use_prefix_count})
                break

        # Page naming (Nuxt)
        pages_dir = os.path.join(base, "pages")
        if os.path.isdir(pages_dir):
            page_files = self._walk_files(pages_dir, (".vue",))
            lower_count = sum(1 for f in page_files if os.path.basename(f)[0].islower())
            result["conventions"].append({"type": "page_naming", "pattern": "lowercase", "count": lower_count})

        return result

    def _conventions_state(self, base: str) -> Dict[str, Any]:
        """Analyze state management conventions."""
        result: Dict[str, Any] = {"conventions": []}

        all_files = self._walk_files(base, (".ts", ".js", ".vue"))

        pinia_count = 0
        vuex_count = 0
        provide_count = 0
        ref_count = 0
        reactive_count = 0

        for fpath in all_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if "defineStore" in content:
                pinia_count += 1
            if "createStore" in content or "useStore" in content:
                vuex_count += 1
            provide_count += len(re.findall(r"\bprovide\s*\(", content))
            ref_count += len(re.findall(r"\bref\s*\(", content))
            reactive_count += len(re.findall(r"\breactive\s*\(", content))

        if pinia_count > 0:
            result["conventions"].append({"type": "store", "pattern": "pinia", "files": pinia_count})
        if vuex_count > 0:
            result["conventions"].append({"type": "store", "pattern": "vuex", "files": vuex_count})
        if provide_count > 0:
            result["conventions"].append({"type": "state", "pattern": "provide/inject", "count": provide_count})

        result["conventions"].append({"type": "reactivity", "ref_count": ref_count, "reactive_count": reactive_count})
        if ref_count > reactive_count:
            result["conventions"].append({"type": "reactivity_preference", "pattern": "ref() preferred over reactive()"})
        elif reactive_count > ref_count:
            result["conventions"].append({"type": "reactivity_preference", "pattern": "reactive() preferred over ref()"})

        return result

    def _conventions_styling(self, base: str) -> Dict[str, Any]:
        """Analyze styling conventions."""
        result: Dict[str, Any] = {"conventions": []}

        vue_files = self._walk_files(base, (".vue",))
        scoped_count = 0
        module_count = 0
        tailwind_count = 0
        unocss_count = 0
        scss_count = 0
        css_count = 0
        no_style_count = 0

        for fpath in vue_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if re.search(r"<style[^>]*\bscoped\b", content):
                scoped_count += 1
            if re.search(r"<style[^>]*\bmodule\b", content):
                module_count += 1
            if re.search(r'class="[^"]*(?:flex|grid|p-|m-|text-|bg-|border-|rounded)', content):
                tailwind_count += 1
            if re.search(r'class="[^"]*(?:uno-|i-)', content):
                unocss_count += 1
            if re.search(r'<style[^>]*lang=["\']scss["\']', content):
                scss_count += 1
            if re.search(r"<style\b", content):
                css_count += 1
            else:
                no_style_count += 1

        if scoped_count > 0:
            result["conventions"].append({"type": "style_scoping", "pattern": "scoped", "count": scoped_count})
        if module_count > 0:
            result["conventions"].append({"type": "style_scoping", "pattern": "css_modules", "count": module_count})
        if tailwind_count > 0:
            result["conventions"].append({"type": "css_framework", "pattern": "tailwind", "count": tailwind_count})
        if unocss_count > 0:
            result["conventions"].append({"type": "css_framework", "pattern": "unocss", "count": unocss_count})
        if scss_count > 0:
            result["conventions"].append({"type": "preprocessor", "pattern": "scss", "count": scss_count})

        # Check for Tailwind config
        if os.path.isfile(os.path.join(base, "tailwind.config.js")) or \
           os.path.isfile(os.path.join(base, "tailwind.config.ts")):
            result["conventions"].append({"type": "css_framework", "config": "tailwind.config found"})

        # Check for UnoCSS config
        if os.path.isfile(os.path.join(base, "uno.config.ts")) or \
           os.path.isfile(os.path.join(base, "unocss.config.ts")):
            result["conventions"].append({"type": "css_framework", "config": "unocss.config found"})

        return result

    def _conventions_testing(self, base: str) -> Dict[str, Any]:
        """Analyze testing conventions."""
        result: Dict[str, Any] = {"conventions": []}

        # Detect test framework
        pkg_json_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_json_path):
            pkg_content = self._read_file(pkg_json_path) or "{}"
            try:
                pkg = json.loads(pkg_content)
                all_deps = {}
                all_deps.update(pkg.get("devDependencies", {}))
                all_deps.update(pkg.get("dependencies", {}))

                if "vitest" in all_deps:
                    result["conventions"].append({"type": "test_framework", "pattern": "vitest", "version": all_deps["vitest"]})
                if "jest" in all_deps:
                    result["conventions"].append({"type": "test_framework", "pattern": "jest", "version": all_deps["jest"]})
                if "cypress" in all_deps:
                    result["conventions"].append({"type": "e2e_framework", "pattern": "cypress", "version": all_deps["cypress"]})
                if "playwright" in all_deps or "@playwright/test" in all_deps:
                    result["conventions"].append({"type": "e2e_framework", "pattern": "playwright"})
                if "@vue/test-utils" in all_deps:
                    result["conventions"].append({"type": "component_testing", "pattern": "@vue/test-utils"})
                if "@testing-library/vue" in all_deps:
                    result["conventions"].append({"type": "component_testing", "pattern": "@testing-library/vue"})
            except json.JSONDecodeError:
                pass

        # Count test files
        test_files = []
        for ext in (".test.ts", ".test.js", ".spec.ts", ".spec.js", ".test.vue"):
            test_files.extend(self._walk_files(base, (ext,)))

        if test_files:
            spec_count = sum(1 for f in test_files if ".spec." in f)
            test_count = sum(1 for f in test_files if ".test." in f)
            result["conventions"].append({
                "type": "test_naming",
                "spec_count": spec_count,
                "test_count": test_count,
                "preferred": "spec" if spec_count > test_count else "test",
            })

        # Check for test directory structure
        if os.path.isdir(os.path.join(base, "__tests__")):
            result["conventions"].append({"type": "test_location", "pattern": "__tests__/"})
        if os.path.isdir(os.path.join(base, "tests")):
            result["conventions"].append({"type": "test_location", "pattern": "tests/"})
        if os.path.isdir(os.path.join(base, "test")):
            result["conventions"].append({"type": "test_location", "pattern": "test/"})

        return result

    def _conventions_auto_imports(self, base: str) -> Dict[str, Any]:
        """Analyze auto-import conventions (Nuxt specific)."""
        result: Dict[str, Any] = {"conventions": []}

        # Check nuxt.config for auto-import settings
        for config_file in ["nuxt.config.ts", "nuxt.config.js"]:
            fpath = os.path.join(base, config_file)
            if not os.path.isfile(fpath):
                continue

            content = self._read_file(fpath) or ""

            # Check imports configuration
            if re.search(r"imports\s*:\s*\{", content):
                result["conventions"].append({"type": "auto_imports", "custom_config": True})

            # Check for disabled auto-imports
            if re.search(r"autoImport\s*:\s*false|imports\s*:\s*false", content):
                result["conventions"].append({"type": "auto_imports", "disabled": True})

            # Check for components auto-import config
            if re.search(r"components\s*:\s*\{", content) or re.search(r"components\s*:\s*true", content):
                result["conventions"].append({"type": "component_auto_import", "enabled": True})

            break

        # Check if .nuxt/imports.d.ts exists for auto-imported items
        imports_dts = os.path.join(base, ".nuxt", "imports.d.ts")
        if os.path.isfile(imports_dts):
            content = self._read_file(imports_dts) or ""
            auto_imported = re.findall(r"const\s+(\w+)\s*:", content)
            if auto_imported:
                result["conventions"].append({
                    "type": "auto_imported_names",
                    "count": len(auto_imported),
                    "sample": auto_imported[:20],
                })

        return result

    # ── 11. get_similar_patterns ─────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """Find similar implementation patterns in the project."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        description_lower = description.lower()
        matches: List[Dict[str, Any]] = []

        # Find best matching pattern categories
        scored_patterns: List[Tuple[str, int]] = []
        for pattern_name, pattern_config in self.PATTERN_REGISTRY.items():
            score = 0
            for keyword in pattern_config["keywords"]:
                if keyword in description_lower:
                    score += 2
            if pattern_name in description_lower:
                score += 3
            if score > 0:
                scored_patterns.append((pattern_name, score))

        scored_patterns.sort(key=lambda x: x[1], reverse=True)
        target_patterns = scored_patterns[:3] if scored_patterns else []

        # If no specific pattern matched, do a broad keyword search
        if not target_patterns:
            keywords = [w for w in description_lower.split() if len(w) > 2]
            target_patterns = [("_custom", 1)]

        all_files = self._walk_files(base, (".vue", ".ts", ".js", ".tsx", ".jsx"))

        for pattern_name, _score in target_patterns:
            if pattern_name == "_custom":
                # Custom keyword search
                keywords = [w for w in description_lower.split() if len(w) > 2]
                for fpath in all_files:
                    fname_lower = os.path.basename(fpath).lower()
                    if any(kw in fname_lower for kw in keywords):
                        content = self._read_file(fpath)
                        rel = os.path.relpath(fpath, base)
                        match_info: Dict[str, Any] = {
                            "file": rel,
                            "match_type": "filename",
                            "matched_keywords": [kw for kw in keywords if kw in fname_lower],
                        }
                        if content:
                            # Count keyword hits in content
                            content_lower = content.lower()
                            hits = sum(1 for kw in keywords if kw in content_lower)
                            match_info["content_relevance"] = hits
                        matches.append(match_info)
                continue

            config = self.PATTERN_REGISTRY[pattern_name]

            for fpath in all_files:
                fname_lower = os.path.basename(fpath).lower()
                rel = os.path.relpath(fpath, base)
                file_score = 0

                # Check filename
                for fp in config["file_patterns"]:
                    if re.search(fp, fname_lower):
                        file_score += 2
                        break

                # Check content
                content = self._read_file(fpath)
                if not content:
                    continue

                code_hits = []
                for cp in config["code_patterns"]:
                    if re.search(cp, content):
                        code_hits.append(cp)
                        file_score += 1

                if file_score >= 2:
                    match_info = {
                        "file": rel,
                        "pattern": pattern_name,
                        "score": file_score,
                    }
                    if code_hits:
                        match_info["matched_patterns"] = len(code_hits)
                    matches.append(match_info)

        # Deduplicate and sort by score
        seen_files: Set[str] = set()
        unique_matches = []
        for m in sorted(matches, key=lambda x: x.get("score", x.get("content_relevance", 0)), reverse=True):
            if m["file"] not in seen_files:
                seen_files.add(m["file"])
                unique_matches.append(m)

        return {
            "status": "success",
            "query": description,
            "total_matches": len(unique_matches),
            "matches": unique_matches[:20],
        }

    # ── 12. get_project_snapshot ─────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """Generate a compact snapshot of the project structure."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        snapshot: Dict[str, Any] = {
            "status": "success",
            "project_type": "nuxt3" if is_nuxt else "vue3",
            "project_name": os.path.basename(base),
        }

        # Directory counts
        dir_counts: Dict[str, int] = {}
        scan_dirs = {
            "pages": "pages",
            "layouts": "layouts",
            "components": "components",
            "composables": "composables",
            "stores": "stores",
            "middleware": "middleware",
            "plugins": "plugins",
            "server/api": "server/api",
            "server/middleware": "server/middleware",
            "utils": "utils",
            "assets": "assets",
            "public": "public",
            # Also check src/ variants for non-Nuxt Vue
            "src/components": "src/components",
            "src/views": "src/views",
            "src/stores": "src/stores",
            "src/composables": "src/composables",
        }

        for label, rel_dir in scan_dirs.items():
            full = os.path.join(base, rel_dir)
            if os.path.isdir(full):
                files = self._walk_files(full)
                dir_counts[label] = len(files)

        snapshot["directories"] = {k: v for k, v in dir_counts.items() if v > 0}

        # Component subdirectory breakdown
        comp_dir = os.path.join(base, "components")
        if not os.path.isdir(comp_dir):
            comp_dir = os.path.join(base, "src", "components")
        if os.path.isdir(comp_dir):
            comp_breakdown: Dict[str, int] = {}
            for item in os.listdir(comp_dir):
                item_path = os.path.join(comp_dir, item)
                if os.path.isdir(item_path):
                    count = len(self._walk_files(item_path, (".vue",)))
                    if count > 0:
                        comp_breakdown[item] = count
            if comp_breakdown:
                snapshot["component_breakdown"] = comp_breakdown

        # Pages list
        pages_dir = os.path.join(base, "pages")
        if os.path.isdir(pages_dir):
            pages = []
            for fpath in self._walk_files(pages_dir, (".vue",)):
                pages.append(os.path.relpath(fpath, pages_dir))
            snapshot["pages"] = sorted(pages)

        # Layouts
        layouts_dir = os.path.join(base, "layouts")
        if os.path.isdir(layouts_dir):
            layouts = [os.path.splitext(os.path.basename(f))[0]
                       for f in self._walk_files(layouts_dir, (".vue",))]
            snapshot["layouts"] = layouts

        # Composables list
        for comp_dir_name in ["composables", "src/composables"]:
            comp_full = os.path.join(base, comp_dir_name)
            if os.path.isdir(comp_full):
                comps = [os.path.splitext(os.path.basename(f))[0]
                         for f in self._walk_files(comp_full, (".ts", ".js"))]
                snapshot["composables"] = comps
                break

        # Store list
        for store_dir_name in ["stores", "src/stores", "store", "src/store"]:
            store_full = os.path.join(base, store_dir_name)
            if os.path.isdir(store_full):
                stores = [os.path.splitext(os.path.basename(f))[0]
                          for f in self._walk_files(store_full, (".ts", ".js"))]
                snapshot["stores"] = stores
                break

        # Middleware list
        mw_dir = os.path.join(base, "middleware")
        if os.path.isdir(mw_dir):
            mws = [os.path.splitext(os.path.basename(f))[0]
                   for f in self._walk_files(mw_dir, (".ts", ".js"))]
            snapshot["middleware"] = mws

        # Server routes summary
        api_dir = os.path.join(base, "server", "api")
        if os.path.isdir(api_dir):
            api_routes = []
            for fpath in self._walk_files(api_dir, (".ts", ".js")):
                api_routes.append(os.path.relpath(fpath, os.path.join(base, "server")))
            snapshot["server_routes"] = api_routes[:30]

        # Nuxt config modules
        if is_nuxt:
            for config_file in ["nuxt.config.ts", "nuxt.config.js"]:
                cfpath = os.path.join(base, config_file)
                if not os.path.isfile(cfpath):
                    continue
                ccontent = self._read_file(cfpath) or ""

                # Extract modules
                modules = []
                for mm in re.finditer(r"modules\s*:\s*\[([^\]]+)\]", ccontent, re.DOTALL):
                    for mod in re.finditer(r"['\"]([^'\"]+)['\"]", mm.group(1)):
                        modules.append(mod.group(1))
                if modules:
                    snapshot["nuxt_modules"] = modules

                # Extract devModules (Nuxt 2 compat) / buildModules
                for key in ["buildModules", "devModules"]:
                    for bm in re.finditer(rf"{key}\s*:\s*\[([^\]]+)\]", ccontent, re.DOTALL):
                        for mod in re.finditer(r"['\"]([^'\"]+)['\"]", bm.group(1)):
                            snapshot.setdefault("nuxt_dev_modules", []).append(mod.group(1))

                break

        # Package.json key deps
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            try:
                pkg = json.loads(self._read_file(pkg_path) or "{}")
                deps = list(pkg.get("dependencies", {}).keys())
                dev_deps = list(pkg.get("devDependencies", {}).keys())

                # Filter for key Vue ecosystem deps
                key_deps = [d for d in deps if any(
                    kw in d for kw in ["vue", "nuxt", "pinia", "vuex", "router", "axios", "tailwind", "uno", "primevue", "vuetify", "quasar", "element-plus", "naive-ui", "headless"]
                )]
                key_dev = [d for d in dev_deps if any(
                    kw in d for kw in ["vitest", "cypress", "playwright", "test", "eslint", "prettier", "typescript"]
                )]
                if key_deps:
                    snapshot["key_dependencies"] = key_deps
                if key_dev:
                    snapshot["key_dev_dependencies"] = key_dev

                # Scripts
                scripts = pkg.get("scripts", {})
                if scripts:
                    snapshot["scripts"] = scripts
            except json.JSONDecodeError:
                pass

        return snapshot

    # ── 13. get_auto_imports ─────────────────────────────────────────

    def get_auto_imports(self) -> Dict[str, Any]:
        """Analyze Nuxt auto-imports: composables, components, utils."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        is_nuxt = self._is_nuxt_project(base)
        if not is_nuxt:
            return {"status": "info", "message": "Auto-imports are a Nuxt feature; this appears to be a plain Vue project"}

        result: Dict[str, Any] = {
            "status": "success",
            "auto_imported_composables": [],
            "auto_imported_components": [],
            "auto_imported_utils": [],
            "potential_conflicts": [],
        }

        # 1. Composables in composables/ directory (auto-imported by Nuxt)
        composables_dir = os.path.join(base, "composables")
        if os.path.isdir(composables_dir):
            for fpath in self._walk_files(composables_dir, (".ts", ".js")):
                content = self._read_file(fpath)
                if not content:
                    continue
                rel = os.path.relpath(fpath, base)
                # Named exports
                for m in re.finditer(r"export\s+(?:function|const)\s+(\w+)", content):
                    result["auto_imported_composables"].append({
                        "name": m.group(1),
                        "file": rel,
                        "type": "named_export",
                    })
                # Default export
                if re.search(r"export\s+default\s+", content):
                    fname = os.path.splitext(os.path.basename(fpath))[0]
                    result["auto_imported_composables"].append({
                        "name": fname,
                        "file": rel,
                        "type": "default_export",
                    })

        # 2. Components in components/ directory (auto-imported by Nuxt)
        components_dir = os.path.join(base, "components")
        if os.path.isdir(components_dir):
            for fpath in self._walk_files(components_dir, (".vue",)):
                rel = os.path.relpath(fpath, base)
                fname = os.path.splitext(os.path.basename(fpath))[0]
                # Nuxt uses directory path for prefix: components/base/Button.vue -> BaseButton
                rel_to_comp = os.path.relpath(fpath, components_dir)
                parts = Path(rel_to_comp).parts
                if parts[-1] == "index.vue":
                    parts = parts[:-1]
                else:
                    parts = list(parts[:-1]) + [os.path.splitext(parts[-1])[0]]
                auto_name = "".join(self._to_pascal_case(p) for p in parts)
                result["auto_imported_components"].append({
                    "name": auto_name,
                    "file": rel,
                })

        # 3. Utils in utils/ directory
        utils_dir = os.path.join(base, "utils")
        if os.path.isdir(utils_dir):
            for fpath in self._walk_files(utils_dir, (".ts", ".js")):
                content = self._read_file(fpath)
                if not content:
                    continue
                rel = os.path.relpath(fpath, base)
                for m in re.finditer(r"export\s+(?:function|const)\s+(\w+)", content):
                    result["auto_imported_utils"].append({
                        "name": m.group(1),
                        "file": rel,
                    })

        # 4. Detect naming conflicts
        all_names: Dict[str, List[str]] = {}
        for item in result["auto_imported_composables"]:
            all_names.setdefault(item["name"], []).append(item["file"])
        for item in result["auto_imported_components"]:
            all_names.setdefault(item["name"], []).append(item["file"])
        for item in result["auto_imported_utils"]:
            all_names.setdefault(item["name"], []).append(item["file"])

        # Also check against Vue/Nuxt built-ins
        builtins = set(self.VUE_COMPOSABLES + self.NUXT_COMPOSABLES + self.VUE_LIFECYCLE_HOOKS)
        for name, files in all_names.items():
            if len(files) > 1:
                result["potential_conflicts"].append({
                    "name": name,
                    "files": files,
                    "type": "duplicate_export",
                })
            if name in builtins:
                result["potential_conflicts"].append({
                    "name": name,
                    "files": files,
                    "type": "shadows_builtin",
                    "message": f"'{name}' shadows a Vue/Nuxt built-in composable",
                })

        # 5. Check nuxt.config for custom auto-import dirs
        for config_file in ["nuxt.config.ts", "nuxt.config.js"]:
            cfpath = os.path.join(base, config_file)
            if not os.path.isfile(cfpath):
                continue
            ccontent = self._read_file(cfpath) or ""

            # imports.dirs config
            dirs_match = re.search(r"imports\s*:\s*\{[^}]*dirs\s*:\s*\[([^\]]+)\]", ccontent, re.DOTALL)
            if dirs_match:
                custom_dirs = re.findall(r"['\"]([^'\"]+)['\"]", dirs_match.group(1))
                result["custom_import_dirs"] = custom_dirs

            # Check for disabled auto-imports
            if re.search(r"imports\s*:\s*\{\s*autoImport\s*:\s*false", ccontent):
                result["auto_import_disabled"] = True

            break

        return result

    # ── 14. pre_edit_check ───────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """Context-aware pre-edit impact check."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        fpath = os.path.join(base, file_path)
        if not os.path.isfile(fpath):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(fpath) or ""
        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "symbol": symbol_name,
            "file_type": self._classify_vue_file(file_path),
            "warnings": [],
            "consumers": [],
        }

        file_type = result["file_type"]

        if file_type == "component":
            self._pre_edit_component(base, file_path, symbol_name, content, result)
        elif file_type == "composable":
            self._pre_edit_composable(base, file_path, symbol_name, content, result)
        elif file_type == "store":
            self._pre_edit_store(base, file_path, symbol_name, content, result)
        elif file_type == "page":
            self._pre_edit_page(base, file_path, symbol_name, content, result)
        elif file_type == "middleware":
            result["warnings"].append({
                "severity": "high",
                "message": f"Middleware changes affect ALL routes using this middleware",
            })
            self._find_middleware_consumers(base, file_path, result)
        elif file_type == "layout":
            result["warnings"].append({
                "severity": "high",
                "message": "Layout changes affect all pages using this layout",
            })
            self._find_layout_consumers(base, file_path, result)
        elif file_type == "server_route":
            self._pre_edit_server_route(base, file_path, symbol_name, content, result)
        elif file_type == "plugin":
            result["warnings"].append({
                "severity": "high",
                "message": "Plugin changes affect the entire application",
            })
        else:
            # Generic: find all references to symbol
            self._find_symbol_references(base, symbol_name, result)

        return result

    def _classify_vue_file(self, file_path: str) -> str:
        """Classify a file by its location/type in Vue/Nuxt project."""
        path_lower = file_path.replace("\\", "/").lower()
        if "/components/" in path_lower or path_lower.startswith("components/"):
            return "component"
        if "/composables/" in path_lower or path_lower.startswith("composables/"):
            return "composable"
        if "/stores/" in path_lower or path_lower.startswith("stores/") or "/store/" in path_lower:
            return "store"
        if "/pages/" in path_lower or path_lower.startswith("pages/"):
            return "page"
        if "/middleware/" in path_lower or path_lower.startswith("middleware/"):
            return "middleware"
        if "/layouts/" in path_lower or path_lower.startswith("layouts/"):
            return "layout"
        if "/server/" in path_lower or path_lower.startswith("server/"):
            return "server_route"
        if "/plugins/" in path_lower or path_lower.startswith("plugins/"):
            return "plugin"
        return "unknown"

    def _pre_edit_component(self, base: str, file_path: str, symbol_name: str, content: str, result: Dict[str, Any]) -> None:
        """Pre-edit check for a component file."""
        sfc = self._parse_vue_sfc(content)
        script = sfc["script_setup"] or sfc["script_options"] or ""
        fname = os.path.splitext(os.path.basename(file_path))[0]
        cname = self._to_pascal_case(fname)

        # Check if symbol is a prop
        props = self._extract_define_props(script)
        prop_names = [p["name"] for p in props]
        if symbol_name in prop_names:
            result["warnings"].append({
                "severity": "high",
                "message": f"'{symbol_name}' is a prop — parent components passing this prop must be updated",
            })

        # Check if symbol is an emit
        emits = self._extract_define_emits(script)
        if symbol_name in emits:
            result["warnings"].append({
                "severity": "high",
                "message": f"'{symbol_name}' is an emit — parent components listening to @{symbol_name} must be updated",
            })

        # Check if symbol is exposed
        expose = self._extract_define_expose(script)
        if symbol_name in expose:
            result["warnings"].append({
                "severity": "medium",
                "message": f"'{symbol_name}' is exposed via defineExpose — components using template ref may break",
            })

        # Find parent components using this component
        parents = self._find_component_parents(base, cname, file_path)
        for p in parents:
            result["consumers"].append({"file": p["file"], "relationship": "parent_component"})

        # Check slots
        if sfc["template"]:
            slots = self._extract_slots(sfc["template"])
            slot_names = [s["name"] for s in slots if s["type"] == "provided"]
            if symbol_name in slot_names:
                result["warnings"].append({
                    "severity": "medium",
                    "message": f"'{symbol_name}' is a slot — consumer components using this slot will be affected",
                })

    def _pre_edit_composable(self, base: str, file_path: str, symbol_name: str, content: str, result: Dict[str, Any]) -> None:
        """Pre-edit check for a composable file."""
        # Check if the symbol is a return value
        return_match = re.search(r"return\s*\{([^}]+)\}", content)
        if return_match:
            returns = [r.strip().split(":")[0].strip() for r in return_match.group(1).split(",")]
            if symbol_name in returns:
                result["warnings"].append({
                    "severity": "high",
                    "message": f"'{symbol_name}' is a return value of this composable — all consumers destructuring it will break",
                })

        # Find composable name
        comp_name_match = re.search(r"export\s+(?:function|const)\s+(use[A-Z]\w*)", content)
        if comp_name_match:
            comp_name = comp_name_match.group(1)
            consumers = self._find_composable_consumers(base, comp_name)
            for c in consumers:
                result["consumers"].append({"file": c["file"], "relationship": "composable_consumer"})

    def _pre_edit_store(self, base: str, file_path: str, symbol_name: str, content: str, result: Dict[str, Any]) -> None:
        """Pre-edit check for a store file."""
        # Find the store name
        store_match = re.search(r"defineStore\s*\(\s*['\"](\w+)['\"]", content)
        if not store_match:
            return

        store_name = store_match.group(1)
        pascal = self._to_pascal_case(store_name)
        hook_name = f"use{pascal}Store"

        # Check if symbol is a state field, action, or getter
        if re.search(rf"\b{re.escape(symbol_name)}\s*=\s*(?:ref|reactive|shallowRef)\s*[<(]", content):
            result["warnings"].append({
                "severity": "high",
                "message": f"'{symbol_name}' is a state field — all components reading store.{symbol_name} will be affected",
            })
        elif re.search(rf"(?:async\s+)?{re.escape(symbol_name)}\s*\(", content):
            result["warnings"].append({
                "severity": "medium",
                "message": f"'{symbol_name}' is an action/getter — all callers of store.{symbol_name}() will be affected",
            })

        # Find all files using the store
        all_files = self._walk_files(base, (".vue", ".ts", ".js"))
        for fpath in all_files:
            fc = self._read_file(fpath)
            if fc and hook_name in fc:
                rel = os.path.relpath(fpath, base)
                if rel != file_path:
                    result["consumers"].append({"file": rel, "relationship": "store_consumer"})

    def _pre_edit_page(self, base: str, file_path: str, symbol_name: str, content: str, result: Dict[str, Any]) -> None:
        """Pre-edit check for a page file."""
        # Check for middleware and layout dependencies
        meta_match = re.search(r"definePageMeta\s*\(\s*\{([^}]+)\}\s*\)", content, re.DOTALL)
        if meta_match:
            meta = meta_match.group(1)
            mw_match = re.search(r"middleware\s*:", meta)
            if mw_match:
                result["warnings"].append({
                    "severity": "info",
                    "message": "This page has middleware — ensure changes are compatible",
                })
            layout_match = re.search(r"layout\s*:\s*['\"](\w+)['\"]", meta)
            if layout_match:
                result["warnings"].append({
                    "severity": "info",
                    "message": f"This page uses layout '{layout_match.group(1)}'",
                })

        # Check for data-fetching that might reference the symbol
        if re.search(rf"useFetch.*{re.escape(symbol_name)}|useAsyncData.*{re.escape(symbol_name)}", content):
            result["warnings"].append({
                "severity": "medium",
                "message": f"'{symbol_name}' is used in data-fetching — server endpoint must be compatible",
            })

    def _pre_edit_server_route(self, base: str, file_path: str, symbol_name: str, content: str, result: Dict[str, Any]) -> None:
        """Pre-edit check for a server route file."""
        # Derive the API URL from file path
        rel = file_path.replace("\\", "/")
        if rel.startswith("server/api/"):
            url_path = "/api/" + rel[11:]
            url_path = re.sub(r"\.\w+$", "", url_path)  # Remove extension
            url_path = re.sub(r"\.(get|post|put|patch|delete)$", "", url_path)
            url_path = re.sub(r"/index$", "", url_path)
            result["api_url"] = url_path

        # Find all client-side fetches to this endpoint
        all_vue_files = self._walk_files(base, (".vue", ".ts", ".js"))
        for fpath in all_vue_files:
            if fpath.startswith(os.path.join(base, "server")):
                continue
            fc = self._read_file(fpath)
            if not fc:
                continue

            api_url = result.get("api_url", "")
            if api_url and api_url in fc:
                rel_f = os.path.relpath(fpath, base)
                result["consumers"].append({"file": rel_f, "relationship": "api_caller"})

        if result["consumers"]:
            result["warnings"].append({
                "severity": "high",
                "message": f"This server route has {len(result['consumers'])} client-side caller(s) — ensure API contract is maintained",
            })

    def _find_middleware_consumers(self, base: str, file_path: str, result: Dict[str, Any]) -> None:
        """Find pages that use a specific middleware."""
        fname = os.path.splitext(os.path.basename(file_path))[0]
        # Remove .global suffix if present
        mw_name = re.sub(r"\.global$", "", fname)

        pages_dir = os.path.join(base, "pages")
        if not os.path.isdir(pages_dir):
            return

        for fpath in self._walk_files(pages_dir, (".vue",)):
            content = self._read_file(fpath) or ""
            if re.search(rf"['\"]" + re.escape(mw_name) + rf"['\"]", content):
                rel = os.path.relpath(fpath, base)
                result["consumers"].append({"file": rel, "relationship": "middleware_consumer"})

        # If global, all pages are affected
        if ".global." in os.path.basename(file_path):
            result["warnings"].append({
                "severity": "high",
                "message": "This is a GLOBAL middleware — ALL routes are affected",
            })

    def _find_layout_consumers(self, base: str, file_path: str, result: Dict[str, Any]) -> None:
        """Find pages that use a specific layout."""
        fname = os.path.splitext(os.path.basename(file_path))[0]

        pages_dir = os.path.join(base, "pages")
        if not os.path.isdir(pages_dir):
            return

        for fpath in self._walk_files(pages_dir, (".vue",)):
            content = self._read_file(fpath) or ""
            if re.search(rf"layout\s*:\s*['\"]" + re.escape(fname) + rf"['\"]", content):
                rel = os.path.relpath(fpath, base)
                result["consumers"].append({"file": rel, "relationship": "layout_consumer"})

        # Default layout is used by pages without explicit layout
        if fname == "default":
            result["warnings"].append({
                "severity": "high",
                "message": "This is the DEFAULT layout — all pages without explicit layout use it",
            })

    def _find_symbol_references(self, base: str, symbol_name: str, result: Dict[str, Any]) -> None:
        """Find all references to a symbol across the project."""
        all_files = self._walk_files(base, (".vue", ".ts", ".js"))
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")

        for fpath in all_files:
            content = self._read_file(fpath)
            if not content:
                continue
            if pattern.search(content):
                rel = os.path.relpath(fpath, base)
                result["consumers"].append({"file": rel, "relationship": "reference"})

    # ── verify_schema ────────────────────────────────────────────────

    def verify_schema(self, model_name: str, fields: list) -> Dict[str, Any]:
        """Verify data model fields across Prisma/Drizzle schemas and TypeScript interfaces."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "model": model_name,
            "expected_fields": fields,
            "prisma_fields": [],
            "drizzle_fields": [],
            "typescript_fields": [],
            "mismatches": [],
        }

        # Scan Prisma schema
        prisma_paths = [
            os.path.join(base, "prisma", "schema.prisma"),
            os.path.join(base, "server", "prisma", "schema.prisma"),
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

        # Scan Drizzle schema
        drizzle_dirs = [
            os.path.join(base, "server", "database", "schema"),
            os.path.join(base, "server", "db", "schema"),
            os.path.join(base, "drizzle"),
        ]
        table_pattern = re.compile(
            rf"(?:export\s+const\s+{re.escape(model_name.lower())}(?:s|Table)?|"
            rf"pgTable|mysqlTable|sqliteTable)\s*\(\s*['\"](?:{re.escape(model_name.lower())}s?)['\"]"
            rf"\s*,\s*\{{(.*?)\}}\s*\)",
            re.DOTALL,
        )
        col_pattern = re.compile(r"(\w+)\s*:\s*(?:text|varchar|integer|boolean|timestamp|serial|uuid|jsonb?|real|numeric)\s*\(")

        for ddir in drizzle_dirs:
            if not os.path.isdir(ddir):
                continue
            for fpath in self._walk_files(ddir, (".ts", ".js")):
                content = self._read_file(fpath) or ""
                tm = table_pattern.search(content)
                if tm:
                    body = tm.group(1)
                    for cm in col_pattern.finditer(body):
                        result["drizzle_fields"].append(cm.group(1))

        # Scan TypeScript interfaces/types
        ts_dirs = [
            os.path.join(base, "types"),
            os.path.join(base, "server", "types"),
            os.path.join(base, "composables"),
            os.path.join(base, "utils"),
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
                        result["typescript_fields"].append(fm.group(1))

        # Check mismatches
        expected_set = set(fields)
        prisma_set = set(result["prisma_fields"])
        drizzle_set = set(result["drizzle_fields"])
        ts_set = set(result["typescript_fields"])

        for field in fields:
            if prisma_set and field not in prisma_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_prisma",
                    "message": f"Field '{field}' not found in Prisma schema",
                })
            if drizzle_set and field not in drizzle_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_drizzle",
                    "message": f"Field '{field}' not found in Drizzle schema",
                })
            if ts_set and field not in ts_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_typescript",
                    "message": f"Field '{field}' not found in TypeScript interface",
                })

        result["status"] = "ok" if not result["mismatches"] else "mismatches_found"
        return result

    # ── detect_transaction_risks ─────────────────────────────────────

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing prisma.$transaction around multiple prisma calls in server routes."""
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
        lines = content.split("\n")

        # Find function/handler boundaries
        handler_pattern = re.compile(
            r"(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(?.*?\)?\s*(?:=>|:\s*EventHandler))",
            re.MULTILINE,
        )
        handlers = list(handler_pattern.finditer(content))

        for idx, hm in enumerate(handlers):
            handler_name = hm.group(1) or hm.group(2) or "anonymous"
            start = hm.start()
            end = handlers[idx + 1].start() if idx + 1 < len(handlers) else len(content)
            body = content[start:end]

            # Count prisma write operations
            prisma_writes = re.findall(
                r"prisma\s*\.\s*\w+\s*\.\s*(?:create|update|delete|upsert|createMany|updateMany|deleteMany)\s*\(",
                body,
            )

            if len(prisma_writes) >= 2:
                has_transaction = bool(re.search(
                    r"prisma\s*\.\s*\$transaction\s*\(", body,
                ))
                if not has_transaction:
                    line_num = content[:start].count("\n") + 1
                    risks.append({
                        "handler": handler_name,
                        "line": line_num,
                        "prisma_operations": len(prisma_writes),
                        "risk": "multiple_writes_without_transaction",
                        "message": f"Handler '{handler_name}' has {len(prisma_writes)} Prisma write operations without $transaction",
                        "suggestion": "Wrap in prisma.$transaction([...]) or prisma.$transaction(async (tx) => { ... })",
                    })

            # Check for sequential reads that could use batch
            prisma_reads = re.findall(
                r"await\s+prisma\s*\.\s*(\w+)\s*\.\s*(?:findUnique|findFirst|findMany)\s*\(",
                body,
            )
            if len(prisma_reads) >= 3:
                line_num = content[:start].count("\n") + 1
                risks.append({
                    "handler": handler_name,
                    "line": line_num,
                    "risk": "sequential_reads",
                    "reads": len(prisma_reads),
                    "message": f"Handler '{handler_name}' has {len(prisma_reads)} sequential Prisma reads -- consider batching",
                    "suggestion": "Use Promise.all() or prisma.$transaction() for parallel execution",
                })

        return {"status": "ok", "file": file_path, "risks": risks, "total_risks": len(risks)}

    # ── get_domain_rules ─────────────────────────────────────────────

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check Nuxt domain-specific rules for file types."""
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
        rel_path = os.path.relpath(full_path, base) if base else file_path

        if re.search(r"server/(?:api|routes)/", rel_path):
            file_type = "server_route"
            # Rule: server routes require error handling with createError
            handler_bodies = re.findall(
                r"(?:export\s+default\s+)?defineEventHandler\s*\(\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{(.*?)\}\s*\)",
                content, re.DOTALL,
            )
            for body in handler_bodies:
                if "createError" not in body and "throw" not in body:
                    violations.append({
                        "rule": "server_route_error_handling",
                        "severity": "warning",
                        "message": "Server route handler lacks error handling -- use createError() for proper error responses",
                    })

        elif re.search(r"composables/", rel_path):
            file_type = "composable"
            # Rule: composables should not make direct API calls in setup
            if re.search(r"(?:useFetch|useAsyncData|\$fetch)\s*\(", content):
                # Check if it's inside a function (OK) or at top level (not OK)
                top_level_fetch = re.search(
                    r"^(?:const|let|var)\s+\w+\s*=\s*(?:await\s+)?(?:useFetch|useAsyncData|\$fetch)\s*\(",
                    content, re.MULTILINE,
                )
                if top_level_fetch:
                    violations.append({
                        "rule": "composable_no_direct_api",
                        "severity": "warning",
                        "message": "Composable has top-level API call -- wrap in a composable function to avoid setup-phase side effects",
                    })

        elif re.search(r"pages/", rel_path) and rel_path.endswith(".vue"):
            file_type = "page"
            # Rule: pages require definePageMeta
            if "definePageMeta" not in content:
                violations.append({
                    "rule": "page_requires_meta",
                    "severity": "info",
                    "message": "Page component lacks definePageMeta -- consider adding layout, middleware, or title metadata",
                })
            # Check for missing head/SEO
            if "useHead" not in content and "useSeoMeta" not in content and "definePageMeta" not in content:
                violations.append({
                    "rule": "page_requires_head",
                    "severity": "info",
                    "message": "Page has no useHead() or useSeoMeta() -- consider adding SEO metadata",
                })

        elif re.search(r"middleware/", rel_path):
            file_type = "middleware"
            # Rule: middleware must return navigateTo
            if "navigateTo" not in content and "abortNavigation" not in content:
                violations.append({
                    "rule": "middleware_requires_navigation",
                    "severity": "warning",
                    "message": "Middleware does not use navigateTo() or abortNavigation() -- may silently pass through",
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
        """Generate a Vitest test skeleton for a Nuxt component, composable, or server route."""
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

        # Detect component props
        props = re.findall(r"defineProps\s*<\s*\{([^}]*)\}", content, re.DOTALL)
        prop_names = []
        if props:
            prop_names = re.findall(r"(\w+)\s*[?:]", props[0])

        # Detect emits
        emits = re.findall(r"defineEmits\s*(?:<[^>]+>)?\(\s*\[([^\]]*)\]", content)
        emit_names = []
        if emits:
            emit_names = re.findall(r"['\"](\w+)['\"]", emits[0])

        if rel_path.endswith(".vue"):
            result["test_type"] = "component"
            props_str = ", ".join(f"{p}: undefined" for p in prop_names[:5]) if prop_names else ""
            emit_tests = "\n".join(
                f"    // expect(wrapper.emitted('{e}')).toBeTruthy();"
                for e in emit_names[:5]
            )
            result["skeleton"] = (
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ mount }} from '@vue/test-utils';\n"
                f"import {symbol} from '~/{rel_path}';\n\n"
                f"describe('{symbol}', () => {{\n"
                f"  it('should mount successfully', () => {{\n"
                f"    const wrapper = mount({symbol}, {{\n"
                f"      props: {{ {props_str} }},\n"
                f"    }});\n"
                f"    expect(wrapper.exists()).toBe(true);\n"
                f"  }});\n\n"
                f"  it('should render correctly', () => {{\n"
                f"    const wrapper = mount({symbol});\n"
                f"    // TODO: Add specific render assertions\n"
                f"    expect(wrapper.html()).toMatchSnapshot();\n"
                f"  }});\n"
                + (f"\n  it('should emit events', () => {{\n"
                   f"    const wrapper = mount({symbol});\n"
                   f"{emit_tests}\n"
                   f"  }});\n" if emit_names else "")
                + f"}});\n"
            )

        elif re.search(r"composables/", rel_path):
            result["test_type"] = "composable"
            result["skeleton"] = (
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ createApp }} from 'vue';\n"
                f"import {{ {symbol} }} from '~/{rel_path.replace('.ts', '').replace('.js', '')}';\n\n"
                f"describe('{symbol}', () => {{\n"
                f"  it('should return expected values', () => {{\n"
                f"    const app = createApp({{ setup() {{ const result = {symbol}(); return {{ result }}; }} }});\n"
                f"    // TODO: Mount app and verify composable return values\n"
                f"  }});\n\n"
                f"  it('should handle reactive state', () => {{\n"
                f"    // TODO: Test reactivity\n"
                f"  }});\n"
                f"}});\n"
            )

        elif re.search(r"server/(?:api|routes)/", rel_path):
            result["test_type"] = "server_route"
            result["skeleton"] = (
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ setup, $fetch }} from '@nuxt/test-utils';\n\n"
                f"describe('{symbol}', () => {{\n"
                f"  await setup({{ server: true }});\n\n"
                f"  it('should return valid response', async () => {{\n"
                f"    const data = await $fetch('/api/{symbol.replace('_', '/')}');\n"
                f"    expect(data).toBeDefined();\n"
                f"    // TODO: Add specific response assertions\n"
                f"  }});\n\n"
                f"  it('should handle errors', async () => {{\n"
                f"    // TODO: Test error scenarios\n"
                f"    await expect($fetch('/api/{symbol.replace('_', '/')}', {{ method: 'POST' }}))\n"
                f"      .rejects.toThrow();\n"
                f"  }});\n"
                f"}});\n"
            )
        else:
            result["test_type"] = "utility"
            result["skeleton"] = (
                f"import {{ describe, it, expect }} from 'vitest';\n"
                f"import {{ {symbol} }} from '~/{rel_path.replace('.ts', '').replace('.js', '')}';\n\n"
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
        """Match server middleware auth to frontend v-if/v-show conditions and definePageMeta middleware."""
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
            "page_meta_guards": [],
            "unmatched": [],
        }

        rel_path = os.path.relpath(full_path, base) if base else file_path

        # Check server middleware for auth checks
        if re.search(r"server/middleware/", rel_path) or re.search(r"middleware/", rel_path):
            # Server-side auth checks
            auth_checks = re.findall(
                r"(?:getServerSession|requireAuth|isAuthenticated|getToken|verifyToken|event\.context\.auth)\s*\(",
                content,
            )
            for ac in auth_checks:
                result["server_guards"].append({
                    "type": "server_middleware",
                    "check": ac.strip("("),
                    "file": rel_path,
                })

            # Check for throw/createError on auth failure
            error_throws = re.findall(
                r"(?:throw\s+createError|createError)\s*\(\s*\{[^}]*(?:401|403|unauthorized|forbidden)[^}]*\}",
                content, re.IGNORECASE,
            )
            for et in error_throws:
                result["server_guards"].append({
                    "type": "auth_error",
                    "detail": et.strip()[:80],
                })

        # Scan Vue components for matching v-if/v-show auth conditions
        pages_dir = os.path.join(base, "pages")
        components_dir = os.path.join(base, "components")
        layouts_dir = os.path.join(base, "layouts")

        for scan_dir in [pages_dir, components_dir, layouts_dir]:
            if not os.path.isdir(scan_dir):
                continue
            for fpath in self._walk_files(scan_dir, (".vue",)):
                fe_content = self._read_file(fpath)
                if not fe_content:
                    continue
                rel = os.path.relpath(fpath, base)

                # Look for auth-related v-if / v-show directives
                auth_directives = re.findall(
                    r'(?:v-if|v-show)\s*=\s*"([^"]*(?:auth|user|logged|session|isAuth|token|role|permission)[^"]*)"',
                    fe_content, re.IGNORECASE,
                )
                for ad in auth_directives:
                    result["client_guards"].append({
                        "file": rel,
                        "directive": ad,
                        "type": "template_conditional",
                    })

                # Look for definePageMeta middleware
                meta_match = re.search(
                    r"definePageMeta\s*\(\s*\{[^}]*middleware\s*:\s*\[?['\"]?(\w+)['\"]?\]?",
                    fe_content,
                )
                if meta_match:
                    mw_name = meta_match.group(1)
                    if re.search(r"auth", mw_name, re.IGNORECASE):
                        result["page_meta_guards"].append({
                            "file": rel,
                            "middleware": mw_name,
                            "type": "page_meta_middleware",
                        })

        # Identify unmatched guards
        has_server = bool(result["server_guards"])
        has_client = bool(result["client_guards"]) or bool(result["page_meta_guards"])

        if has_server and not has_client:
            result["unmatched"].append({
                "severity": "warning",
                "message": "Server auth middleware exists but no matching client-side auth conditionals found",
            })
        if has_client and not has_server:
            result["unmatched"].append({
                "severity": "error",
                "message": "Client-side auth checks exist but no server middleware auth guard found -- potential security issue",
            })

        result["status"] = "ok"
        return result
