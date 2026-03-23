"""SvelteKit plugin for Blindspot MCP.

Provides SvelteKit-specific intelligence tools:
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

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class SvelteKitPlugin(BlindspotPlugin):
    """SvelteKit framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "sveltekit"

    @property
    def framework(self) -> str:
        return "sveltekit"

    @property
    def description(self) -> str:
        return "SvelteKit framework intelligence — components, routes, stores, load functions, form actions, hooks"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "routes": "src/routes",
            "components": "src/lib/components",
            "stores": "src/lib/stores",
            "utils": "src/lib",
            "hooks": "src",
            "api": "src/routes/api",
            "params": "src/params",
            "tests": "tests",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\b(?:document|window)\b(?!.*(?:browser|typeof\s+window))",
                "severity": "error",
                "message": "Direct document/window access without browser guard — use `import { browser } from '$app/environment'` check",
                "file_types": ["svelte", "ts", "js"],
            },
            {
                "pattern": r"\bconsole\.log\s*\(",
                "severity": "warning",
                "message": "console.log() in code — remove before commit",
                "file_types": ["svelte", "ts", "js"],
            },
            {
                "pattern": r"\bdebugger\b",
                "severity": "error",
                "message": "debugger statement in code — remove before commit",
                "file_types": ["svelte", "ts", "js"],
            },
            {
                "pattern": r"derived\s*\([^)]*\b(?:push|pop|splice|shift|unshift|reverse|sort)\b",
                "severity": "error",
                "message": "Mutable operation in derived store — derived stores must return new values, not mutate",
                "file_types": ["ts", "js"],
            },
            {
                "pattern": r"\$:\s*(?:await\s|async\s)",
                "severity": "error",
                "message": "Async operation in reactive statement ($:) — use onMount or a reactive store instead",
                "file_types": ["svelte"],
            },
            {
                "pattern": r"bind:\w+(?=.*(?:<!--.*two-way|//.*two-way))",
                "severity": "warning",
                "message": "bind: directive creates two-way binding — ensure this is intentional and not just for reading",
                "file_types": ["svelte"],
            },
            {
                "pattern": r"@html\b",
                "severity": "warning",
                "message": "{@html} renders raw HTML — XSS risk, ensure input is sanitized",
                "file_types": ["svelte"],
            },
            {
                "pattern": r"goto\s*\(\s*['\"`][^'\"`]*['\"`]\s*\)",
                "severity": "warning",
                "message": "Client-side goto() — consider using form actions or load functions for data mutations",
                "file_types": ["svelte", "ts", "js"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all SvelteKit-specific MCP tools."""
        from ...services.sveltekit_intelligence_service import SvelteKitIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        # ── 1. get_sveltekit_component_tree ──────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_component_tree(ctx: Context, component_name: str = None) -> dict[str, Any]:
            """
            Scan Svelte component directories and extract the component tree.
            Returns: props (export let), events (dispatch), slots, reactive statements ($:),
            store subscriptions ($store), context (getContext/setContext), lifecycle hooks.

            Args:
                component_name: Optional specific component name (e.g., "Button").
                                If omitted, returns all discovered components.
            """
            return SvelteKitIntelligenceService(ctx).get_component_tree(component_name)

        # ── 2. get_sveltekit_route_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse SvelteKit file-based routing from src/routes.
            Returns: +page.svelte, +page.ts (load), +page.server.ts (server load, form actions),
            +server.ts (API endpoints), +layout.svelte, +error.svelte for each route.

            Args:
                filter_prefix: Optional route prefix filter (e.g., "/api", "/dashboard")
            """
            return SvelteKitIntelligenceService(ctx).get_route_map(filter_prefix)

        # ── 3. get_sveltekit_store_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_store_map(ctx: Context, store_name: str = None) -> dict[str, Any]:
            """
            Parse Svelte stores: writable(), readable(), derived(), custom stores.
            Maps each store to its subscriber components ($store usage).

            Args:
                store_name: Optional specific store name (e.g., "currentUser").
                            If omitted, returns all discovered stores.
            """
            return SvelteKitIntelligenceService(ctx).get_store_map(store_name)

        # ── 4. get_sveltekit_component_dependencies ──────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_component_dependencies(component_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace all dependencies for a Svelte component file.
            Returns: parent importers (with passed props and event listeners),
            child components, props (export let), slots, events dispatched,
            stores subscribed, context provided/consumed.

            Args:
                component_path: Relative path to the component file
                                (e.g., "src/lib/components/Button.svelte")
            """
            return SvelteKitIntelligenceService(ctx).get_component_dependencies(component_path)

        # ── 5. get_sveltekit_flow_map ────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete request flow from entry point through the SvelteKit pipeline.
            Route -> hooks.server (handle/handleError/handleFetch) -> layout chain ->
            page load -> server load -> form action -> component render.

            Args:
                entry_point: Route path (e.g., "/dashboard") or file path
                             (e.g., "src/routes/dashboard/+page.svelte")
                method: Optional HTTP method (e.g., "POST"). If omitted, traces GET flow.
            """
            return SvelteKitIntelligenceService(ctx).get_flow_map(entry_point, method)

        # ── 6. get_sveltekit_load_functions ──────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_load_functions(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Analyze all SvelteKit load functions across the project.
            Covers: +page.ts (universal), +page.server.ts (server-only),
            +layout.ts, +layout.server.ts. Extracts: fetch calls, depends(),
            parent(), returned data keys, cookies/locals usage, streaming.

            Args:
                route_path: Optional route path (e.g., "/dashboard").
                            If omitted, returns load functions for all routes.
            """
            return SvelteKitIntelligenceService(ctx).get_load_functions(route_path)

        # ── 7. get_sveltekit_middleware_chain ─────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse hooks.server.ts (handle sequence), hooks.client.ts, and
            +layout.server.ts auth checks. Trace middleware applied to each route.

            Args:
                route_path: Optional route path to check (e.g., "/api/users").
                            If omitted, returns full middleware/hooks config.
            """
            return SvelteKitIntelligenceService(ctx).get_middleware_chain(route_path)

        # ── 8. get_sveltekit_form_actions ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_form_actions(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse +page.server.ts form actions: export const actions = { default, named }.
            Extracts form data parsing, validation, fail() returns, redirect().
            Cross-references with +page.svelte form fields to detect mismatches.

            Args:
                route_path: Optional route path (e.g., "/login").
                            If omitted, returns form actions for all routes.
            """
            return SvelteKitIntelligenceService(ctx).get_form_actions(route_path)

        # ── 9. verify_sveltekit_endpoint ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_sveltekit_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a SvelteKit endpoint: route file exists (+page.svelte or +server.ts),
            handler exports the HTTP method, load function returns data,
            form actions handle POST, error boundaries present.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return SvelteKitIntelligenceService(ctx).verify_endpoint(method, url)

        # ── 10. get_sveltekit_project_conventions ────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real coding conventions from the SvelteKit project codebase.
            Analyzes actual files to determine the project's established patterns.

            Args:
                pattern_type: One of: "naming" (file/component naming),
                              "state" (stores vs context vs load),
                              "styling" (scoped/<style>/Tailwind),
                              "testing" (Playwright/Vitest),
                              "loading" (streaming/preload patterns)
            """
            return SvelteKitIntelligenceService(ctx).get_project_conventions(pattern_type)

        # ── 11. get_sveltekit_similar_patterns ───────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the SvelteKit project.
            Use before implementing new features to discover existing solutions.
            Searches for: modal, form-action, auth-guard, toast, dropdown, table,
            pagination, search, layout, error-boundary patterns.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "modal dialog", "form with validation", "auth guard")
            """
            return SvelteKitIntelligenceService(ctx).get_similar_patterns(description)

        # ── 12. get_sveltekit_project_snapshot ───────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_sveltekit_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire SvelteKit project structure.
            Returns: routes (pages/layouts/server endpoints), components, stores,
            hooks, lib utilities, svelte.config settings, key dependencies,
            environment files, app.d.ts type declarations.
            """
            return SvelteKitIntelligenceService(ctx).get_project_snapshot()

        # ── 13. pre_sveltekit_edit_check ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_sveltekit_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a SvelteKit project.
            Context-aware analysis based on file type:
            - +page -> check load functions and layout data
            - component -> check importers, props, events, slots
            - store -> check all subscriber components ($store usage)
            - hook -> warn about global impact on all routes
            - +layout -> check all child routes affected

            Args:
                file_path: Relative file path (e.g., "src/lib/components/Button.svelte")
                symbol_name: Symbol to check (e.g., "handleClick", "UserCard", "currentUser")
            """
            return SvelteKitIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["SvelteKitPlugin"]
