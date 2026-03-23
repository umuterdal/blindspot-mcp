"""Vue 3 / Nuxt 3 plugin for Blindspot MCP.

Provides Vue/Nuxt-specific intelligence tools:
- Component tree parsing (SFC, script setup, defineProps/Emits/Expose)
- Nuxt server route mapping (server/api/)
- Composable extraction and consumer mapping
- Component dependency tracing (props, emits, slots, provide/inject)
- Flow mapping (page -> middleware -> layout -> server API)
- State management discovery (Pinia, Vuex, provide/inject)
- Middleware chain resolution
- Plugin discovery (defineNuxtPlugin, app.use)
- Auto-import analysis and conflict detection
- Project convention extraction
- Similar pattern discovery
- Pre-edit impact analysis
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class NuxtPlugin(BlindspotPlugin):
    """Vue 3 / Nuxt 3 framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "nuxt"

    @property
    def framework(self) -> str:
        return "nuxt"

    @property
    def description(self) -> str:
        return "Vue 3 / Nuxt 3 framework intelligence — components, composables, server routes, Pinia, middleware, auto-imports"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "components": "components",
            "pages": "pages",
            "layouts": "layouts",
            "composables": "composables",
            "stores": "stores",
            "middleware": "middleware",
            "plugins": "plugins",
            "server": "server",
            "api": "server/api",
            "utils": "utils",
            "assets": "assets",
            "public": "public",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bconsole\.log\s*\(",
                "severity": "warning",
                "message": "console.log() in code — remove before commit",
                "file_types": ["vue", "ts", "js"],
            },
            {
                "pattern": r"\bconsole\.debug\s*\(",
                "severity": "warning",
                "message": "console.debug() in code — remove before commit",
                "file_types": ["vue", "ts", "js"],
            },
            {
                "pattern": r"\bv-html\b",
                "severity": "error",
                "message": "v-html directive — XSS risk, ensure input is sanitized",
                "file_types": ["vue"],
            },
            {
                "pattern": r"\$refs\.",
                "severity": "warning",
                "message": "$refs in Composition API — use template refs (const myRef = ref(null)) instead",
                "file_types": ["vue", "ts", "js"],
            },
            {
                "pattern": r"\breactive\s*\(\s*(?:['\"]|[0-9]|true|false|null)",
                "severity": "warning",
                "message": "reactive() used for primitive value — use ref() instead for primitives",
                "file_types": ["vue", "ts", "js"],
            },
            {
                "pattern": r"\bdata\s*\(\s*\)\s*\{",
                "severity": "warning",
                "message": "Options API data() in Composition API project — use ref()/reactive() in <script setup>",
                "file_types": ["vue"],
            },
            {
                "pattern": r"\bmethods\s*:\s*\{",
                "severity": "warning",
                "message": "Options API methods: {} in Composition API project — use functions in <script setup>",
                "file_types": ["vue"],
            },
            {
                "pattern": r"\bcomputed\s*:\s*\{",
                "severity": "warning",
                "message": "Options API computed: {} — use computed() from vue in <script setup>",
                "file_types": ["vue"],
            },
            {
                "pattern": r"\bdebugger\b",
                "severity": "error",
                "message": "debugger statement in code — remove before commit",
                "file_types": ["vue", "ts", "js"],
            },
            {
                "pattern": r"@ts-ignore",
                "severity": "warning",
                "message": "@ts-ignore suppresses type errors — use @ts-expect-error with explanation",
                "file_types": ["ts", "vue"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Vue/Nuxt-specific MCP tools."""
        from ...services.nuxt_intelligence_service import NuxtIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        # ── 1. get_nuxt_component_tree ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_component_tree(ctx: Context, component_name: str = None) -> dict[str, Any]:
            """
            Parse Vue 3 SFC (.vue) files and build the component tree.
            Returns: props (defineProps), emits (defineEmits), expose (defineExpose),
            composables used, child components in template, slot usage, style info.
            Works with both Nuxt 3 and plain Vue 3 projects.

            Args:
                component_name: Optional specific component name (e.g., "UserCard").
                                If omitted, returns all discovered components.
            """
            return NuxtIntelligenceService(ctx).get_component_tree(component_name)

        # ── 2. get_nuxt_api_routes ───────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_api_routes(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse API routes from Nuxt server routes (server/api/**/*.ts with
            defineEventHandler) or Vue Router route definitions.
            Returns: route path, HTTP method, handler info, auth checks, validation.

            Args:
                filter_prefix: Optional route prefix filter (e.g., "/api/users")
            """
            return NuxtIntelligenceService(ctx).get_api_routes(filter_prefix)

        # ── 3. get_nuxt_composable_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_composable_map(ctx: Context, composable_name: str = None) -> dict[str, Any]:
            """
            Scan composables/ directory for useXxx() functions and map consumers.
            Returns: reactive/ref state, computed, watchers, lifecycle hooks, return values,
            and which components/files use each composable.

            Args:
                composable_name: Optional specific composable name (e.g., "useAuth").
                                 If omitted, returns all discovered composables.
            """
            return NuxtIntelligenceService(ctx).get_composable_map(composable_name)

        # ── 4. get_nuxt_component_dependencies ───────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_component_dependencies(component_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace all dependencies for a Vue component file.
            Returns: parent components importing it, child components in template,
            props interface, emits, slots provided/consumed, composables injected,
            provide/inject keys.

            Args:
                component_path: Relative path to the .vue file
                                (e.g., "components/UserCard.vue")
            """
            return NuxtIntelligenceService(ctx).get_component_dependencies(component_path)

        # ── 5. get_nuxt_flow_map ─────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete request flow from entry point through the stack.
            Nuxt: page.vue -> middleware -> layout -> server API -> database.
            Vue: route -> guard -> component -> store -> API call.

            Args:
                entry_point: Page path (e.g., "pages/users/index.vue"),
                             URL (e.g., "/api/users"), or file path
                method: Optional HTTP method for API routes (e.g., "POST")
            """
            return NuxtIntelligenceService(ctx).get_flow_map(entry_point, method)

        # ── 6. get_nuxt_state_management ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_state_management(ctx: Context) -> dict[str, Any]:
            """
            Discover all state management in the Vue/Nuxt project.
            Finds: Pinia stores (defineStore — setup and options style),
            Vuex stores (createStore), provide/inject patterns.
            Maps store state, actions, getters, and consumer components.
            """
            return NuxtIntelligenceService(ctx).get_state_management()

        # ── 7. get_nuxt_middleware_chain ──────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse middleware directory and resolve the middleware chain.
            Nuxt: middleware/ with defineNuxtRouteMiddleware, global middlewares.
            Vue: Vue Router beforeEach, beforeResolve, afterEach, beforeEnter guards.

            Args:
                route_path: Optional route path to check applicable middlewares
                            (e.g., "/dashboard"). If omitted, returns all middlewares.
            """
            return NuxtIntelligenceService(ctx).get_middleware_chain(route_path)

        # ── 8. get_nuxt_plugin_map ───────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_plugin_map(ctx: Context) -> dict[str, Any]:
            """
            Discover all plugins in the project.
            Nuxt: plugins/ directory with defineNuxtPlugin, client/server modes.
            Vue: main.ts app.use() registrations.
            Returns: plugin names, modes, provided values, dependencies.
            """
            return NuxtIntelligenceService(ctx).get_plugin_map()

        # ── 9. verify_nuxt_endpoint ──────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_nuxt_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Nuxt server route endpoint: route file exists,
            defineEventHandler present, method matches filename convention,
            auth check, input validation, error handling, server middleware.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return NuxtIntelligenceService(ctx).verify_endpoint(method, url)

        # ── 10. get_nuxt_project_conventions ─────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real coding conventions from the Vue/Nuxt project codebase.
            Analyzes actual files to determine established patterns.

            Args:
                pattern_type: One of: "naming" (component/composable naming conventions),
                              "state" (Pinia vs Vuex, ref vs reactive),
                              "styling" (scoped/Tailwind/UnoCSS/SCSS),
                              "testing" (Vitest/Cypress/Playwright),
                              "auto-imports" (Nuxt auto-import configuration)
            """
            return NuxtIntelligenceService(ctx).get_project_conventions(pattern_type)

        # ── 11. get_nuxt_similar_patterns ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the Vue/Nuxt project.
            Use before implementing new features to discover existing solutions.
            Searches for: modal, dropdown, form, table, tabs, toast, sidebar,
            carousel, dialog, drawer, tooltip, popover patterns.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "modal dialog", "dropdown menu", "data table with sorting")
            """
            return NuxtIntelligenceService(ctx).get_similar_patterns(description)

        # ── 12. get_nuxt_project_snapshot ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire Vue/Nuxt project structure.
            Returns: pages, layouts, components by directory, composables, stores,
            middleware, plugins, server routes, nuxt.config modules, key dependencies.
            ~5KB overview useful for project orientation.
            """
            return NuxtIntelligenceService(ctx).get_project_snapshot()

        # ── 13. get_nuxt_auto_imports ────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nuxt_auto_imports(ctx: Context) -> dict[str, Any]:
            """
            Analyze Nuxt auto-imports: which composables, components, and utils
            are auto-imported, custom import directories, and potential naming
            conflicts (duplicate exports or shadowing Vue/Nuxt built-ins).
            """
            return NuxtIntelligenceService(ctx).get_auto_imports()

        # ── 14. pre_nuxt_edit_check ──────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_nuxt_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a Vue/Nuxt project.
            Context-aware analysis based on file type:
            - Component: check consumers, props, emits, slots, template refs
            - Composable: check all composable consumers, return values
            - Store: check all subscriber components, state/action references
            - Page: check middleware, layout, data-fetching dependencies
            - Middleware: warn about broad route impact (especially global)
            - Layout: warn about all pages using this layout
            - Server route: find client-side API callers

            Args:
                file_path: Relative file path (e.g., "components/UserCard.vue")
                symbol_name: Symbol to check (e.g., "handleClick", "userName", "useAuth")
            """
            return NuxtIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["NuxtPlugin"]
