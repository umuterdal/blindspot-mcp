"""Next.js / React plugin for Blindspot MCP.

Provides Next.js-specific intelligence tools:
- React component tree and dependency mapping
- App Router and Pages Router API route parsing
- Prisma schema extraction and relation mapping
- State management discovery (Zustand, Redux, Context)
- Middleware chain resolution
- Data-fetching pattern detection (Server Components, React Query, SWR)
- Validation chain tracing (Zod/Yup)
- Project convention extraction
- Similar pattern discovery
- Pre-edit impact analysis
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class NextjsPlugin(BlindspotPlugin):
    """Next.js / React framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "nextjs"

    @property
    def framework(self) -> str:
        return "nextjs"

    @property
    def description(self) -> str:
        return "Next.js / React framework intelligence — components, API routes, Prisma, state, data-fetching"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "components": "src/components",
            "app": "src/app",
            "pages": "pages",
            "api": "src/app/api",
            "lib": "src/lib",
            "hooks": "src/hooks",
            "stores": "src/stores",
            "utils": "src/utils",
            "types": "src/types",
            "styles": "src/styles",
            "public": "public",
            "prisma": "prisma",
            "config": ".",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bconsole\.log\s*\(",
                "severity": "warning",
                "message": "console.log() in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"\bconsole\.debug\s*\(",
                "severity": "warning",
                "message": "console.debug() in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"\bdebugger\b",
                "severity": "error",
                "message": "debugger statement in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"\bany\b(?:\s*[;,)\]}]|\s*$)",
                "severity": "warning",
                "message": "Explicit 'any' type — use a specific type instead",
                "file_types": ["ts", "tsx"],
            },
            {
                "pattern": r"@ts-ignore",
                "severity": "warning",
                "message": "@ts-ignore suppresses type errors — use @ts-expect-error with explanation",
                "file_types": ["ts", "tsx"],
            },
            {
                "pattern": r"useEffect\s*\(\s*(?:async|\(\)\s*=>\s*\{[^}]*fetch)",
                "severity": "warning",
                "message": "Async operation in useEffect — consider React Query, SWR, or server component",
                "file_types": ["tsx", "jsx"],
            },
            {
                "pattern": r"dangerouslySetInnerHTML",
                "severity": "error",
                "message": "dangerouslySetInnerHTML — XSS risk, ensure input is sanitized",
                "file_types": ["tsx", "jsx"],
            },
            {
                "pattern": r"process\.env\.\w+(?!\s*[!?])",
                "severity": "warning",
                "message": "Direct process.env access — use a validated config module for type safety",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Next.js-specific MCP tools."""
        from ...services.nextjs_intelligence_service import NextjsIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        # ── 1. get_component_tree ─────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_component_tree(ctx: Context, component_name: str = None) -> dict[str, Any]:
            """
            Scan React component directories and extract the component tree.
            Returns: props interface, imports, child components, hooks used, client/server status.

            Args:
                component_name: Optional specific component name (e.g., "Button").
                                If omitted, returns all discovered components.
            """
            return NextjsIntelligenceService(ctx).get_component_tree(component_name)

        # ── 2. get_api_routes ─────────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_api_routes(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse Next.js API routes from App Router (route.ts) and Pages Router (pages/api/).
            Returns: HTTP methods exported, route params, auth checks, response types.

            Args:
                filter_prefix: Optional route prefix filter (e.g., "/api/users")
            """
            return NextjsIntelligenceService(ctx).get_api_routes(filter_prefix)

        # ── 3. get_prisma_schema ──────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_prisma_schema(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Parse prisma/schema.prisma and extract models, fields, relations, enums, indexes.
            Returns structured schema with field types, defaults, constraints, and relation mappings.

            Args:
                model_name: Optional specific model name (e.g., "User").
                            If omitted, returns all models.
            """
            return NextjsIntelligenceService(ctx).get_prisma_schema(model_name)

        # ── 4. get_component_dependencies ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_component_dependencies(component_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace all dependencies for a React component file.
            Returns: parent components importing it, child components it uses,
            props interface, hooks, context providers and consumers.

            Args:
                component_path: Relative path to the component file
                                (e.g., "src/components/Button.tsx")
            """
            return NextjsIntelligenceService(ctx).get_component_dependencies(component_path)

        # ── 5. get_nextjs_flow_map ────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete request flow from entry point to response.
            For API routes: route -> middleware -> handler -> service -> Prisma -> response.
            For pages: page.tsx -> server action -> API call -> DB query.

            Args:
                entry_point: File path (e.g., "src/app/api/users/route.ts")
                             or URL path (e.g., "/api/users")
                method: Optional HTTP method (e.g., "POST"). If omitted, traces all methods.
            """
            return NextjsIntelligenceService(ctx).get_flow_map(entry_point, method)

        # ── 6. get_state_management ───────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_state_management(ctx: Context) -> dict[str, Any]:
            """
            Discover all state management in the project.
            Finds: Zustand stores (create()), Redux slices (createSlice),
            React Context (createContext), Jotai atoms, Recoil atoms.
            Maps store-to-consumer relationships.
            """
            return NextjsIntelligenceService(ctx).get_state_management()

        # ── 7. get_nextjs_middleware_chain ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse middleware.ts and extract middleware logic, matcher config,
            auth checks, redirects, header modifications, and route matching.

            Args:
                route_path: Optional route path to check if middleware matches
                            (e.g., "/api/users"). If omitted, returns full middleware config.
            """
            return NextjsIntelligenceService(ctx).get_middleware_chain(route_path)

        # ── 8. get_data_fetching ──────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_data_fetching(file_path: str, ctx: Context) -> dict[str, Any]:
            """
            Detect data-fetching patterns in a Next.js file.
            Identifies: Server vs Client components, getServerSideProps/getStaticProps,
            fetch() with cache options, React Query/SWR hooks, Prisma direct queries,
            server actions, and loading/error boundaries.

            Args:
                file_path: Relative path to the file (e.g., "src/app/users/page.tsx")
            """
            return NextjsIntelligenceService(ctx).get_data_fetching(file_path)

        # ── 9. verify_nextjs_endpoint ─────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_nextjs_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Next.js API endpoint: route file exists, handler exports the HTTP method,
            middleware matches, auth checks present, input validation, error handling.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return NextjsIntelligenceService(ctx).verify_endpoint(method, url)

        # ── 10. get_nextjs_project_conventions ────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real coding conventions from the Next.js project codebase.
            Analyzes actual files to determine the project's established patterns.

            Args:
                pattern_type: One of: "naming" (file/component naming),
                              "state" (state management patterns),
                              "data-fetching" (how data is loaded),
                              "styling" (Tailwind/CSS modules/styled-components),
                              "testing" (test framework and patterns)
            """
            return NextjsIntelligenceService(ctx).get_project_conventions(pattern_type)

        # ── 11. get_nextjs_similar_patterns ───────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the project.
            Use before implementing new features to discover existing solutions.
            Searches for: modal, dropdown, form, table, pagination, auth, upload,
            toast, search, infinite-scroll, tabs, accordion, sidebar, dialog patterns.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "modal dialog", "file upload", "data table with pagination")
            """
            return NextjsIntelligenceService(ctx).get_similar_patterns(description)

        # ── 12. get_nextjs_project_snapshot ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire Next.js project structure.
            Returns: component count by directory, API routes, Prisma models,
            pages, layouts, middleware, state stores, key dependencies, scripts.
            ~5KB overview useful for project orientation.
            """
            return NextjsIntelligenceService(ctx).get_project_snapshot()

        # ── 13. get_nextjs_validation_chain ───────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nextjs_validation_chain(route_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the validation chain for a route: Zod/Yup schema definition ->
            API route handler validation -> form component fields.
            Detects mismatches between schema fields and form inputs.

            Args:
                route_path: API route path (e.g., "/api/users") or file path
                            (e.g., "src/app/api/users/route.ts")
            """
            return NextjsIntelligenceService(ctx).get_validation_chain(route_path)

        # ── 14. pre_nextjs_edit_check ─────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_nextjs_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a Next.js project.
            Context-aware analysis based on file type:
            - Component: check consumers, props, JSX usage
            - API route: check fetch callers, middleware
            - Hook: check all hook users
            - Store: check all subscribers
            - Layout/Middleware: warn about broad impact

            Args:
                file_path: Relative file path (e.g., "src/components/Button.tsx")
                symbol_name: Symbol to check (e.g., "handleClick", "UserCard", "useAuth")
            """
            return NextjsIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["NextjsPlugin"]
