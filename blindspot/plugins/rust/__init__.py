"""Rust Web plugin for Blindspot MCP.

Provides Rust web framework-specific intelligence tools:
- Struct / impl / trait mapping with derive macros
- Multi-framework route parsing (Actix-web, Axum, Rocket)
- Diesel / SQLx / SeaORM schema extraction
- Middleware and Tower layer resolution
- Trait implementation mapping
- Error handling analysis (thiserror, anyhow, Result chains)
- Flow mapping (route -> handler -> service -> DB)
- Project convention detection
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class RustPlugin(BlindspotPlugin):
    """Rust web framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "rust"

    @property
    def framework(self) -> str:
        return "rust"

    @property
    def description(self) -> str:
        return "Rust web framework intelligence -- Actix/Axum/Rocket, Diesel/SQLx/SeaORM, traits, error handling"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "handlers": "src/handlers",
            "models": "src/models",
            "services": "src/services",
            "middleware": "src/middleware",
            "routes": "src/routes",
            "schema": "src/schema",
            "errors": "src/errors",
            "tests": "tests",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\.unwrap\s*\(\s*\)",
                "severity": "warning",
                "message": ".unwrap() in production code -- use ? operator or proper error handling",
                "file_types": ["rs"],
            },
            {
                "pattern": r"\bprintln!\s*\(",
                "severity": "warning",
                "message": "println! in production code -- use tracing or log crate instead",
                "file_types": ["rs"],
            },
            {
                "pattern": r"\.clone\s*\(\s*\)",
                "severity": "info",
                "message": ".clone() detected -- verify this clone is necessary",
                "file_types": ["rs"],
            },
            {
                "pattern": r"\bunsafe\s*\{",
                "severity": "warning",
                "message": "unsafe block -- ensure it has a SAFETY comment documenting invariants",
                "file_types": ["rs"],
            },
            {
                "pattern": r"\.expect\s*\(\s*\"",
                "severity": "info",
                "message": ".expect() in production code -- consider ? operator for recoverable errors",
                "file_types": ["rs"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Rust web-specific MCP tools."""
        from ...services.rust_intelligence_service import RustIntelligenceService
        from ...utils import handle_mcp_tool_errors

        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_struct_map(ctx: Context, struct_name: str = None) -> dict[str, Any]:
            """
            Parse Rust struct definitions, derive macros, impl blocks, and trait
            implementations. Maps struct -> fields -> derives -> impl blocks -> traits.

            Args:
                struct_name: Optional specific struct name (e.g., "User").
                            If omitted, returns all structs.
            """
            return RustIntelligenceService(ctx).get_struct_map(struct_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse routes from Actix-web (#[get("/")]), Axum (Router::new().route()),
            and Rocket (#[get("/")]). Extracts route path, HTTP method, handler,
            extractors, and guards.

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/api")
            """
            return RustIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_schema_info(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Diesel schema.rs (table! macro), SQLx FromRow structs, and SeaORM
            entity definitions to extract database schema information.

            Args:
                table_name: Optional table or struct name (e.g., "users").
                           If omitted, returns schema for all tables.
            """
            return RustIntelligenceService(ctx).get_schema_info(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete Rust web request flow from route to response.
            Traces: Route -> Middleware/Layer -> Handler -> Service ->
            Repository -> DB query -> Response.

            Args:
                entry_point: Handler function name (e.g., "get_user")
                method: Not used for Rust (handlers are standalone functions)
            """
            return RustIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse Actix-web middleware (.wrap()), Axum layers (.layer()),
            and Tower middleware (ServiceBuilder). Extracts per-route stacks.

            Args:
                route_path: Optional route path to filter middleware.
                           If omitted, returns all middleware.
            """
            return RustIntelligenceService(ctx).get_middleware_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_trait_implementations(ctx: Context, trait_name: str = None) -> dict[str, Any]:
            """
            Find all `impl TraitName for Type` blocks across the project.
            Essential for understanding Rust's polymorphism. Maps trait -> implementors
            and extracts method signatures.

            Args:
                trait_name: Optional trait name (e.g., "FromRequest").
                           If omitted, returns all traits.
            """
            return RustIntelligenceService(ctx).get_trait_implementations(trait_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_error_handling(ctx: Context, handler_name: str = None) -> dict[str, Any]:
            """
            Parse Rust error handling: Result<T,E> return types, ? operator chains,
            custom error types (thiserror/anyhow), and ResponseError/IntoResponse impls.

            Args:
                handler_name: Optional handler function name to analyze.
                             If omitted, returns all custom error types.
            """
            return RustIntelligenceService(ctx).get_error_handling(handler_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_rust_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Rust web endpoint: route registered, handler exists,
            extractors match. Returns pass/warning/fail status.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users/{id}")
            """
            return RustIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Rust web project codebase.

            Args:
                pattern_type: One of: "naming" (snake_case/PascalCase compliance),
                             "error-handling" (Result patterns, thiserror vs anyhow),
                             "async" (tokio patterns, spawn usage),
                             "testing" (#[cfg(test)] module patterns),
                             "module-structure" (src/ layout analysis)
            """
            return RustIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Get a high-level overview of the Rust web project.
            Returns: Cargo.toml info, web framework detected, structs, traits,
            routes, middleware, test count, and module structure.
            """
            return RustIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rust_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the Rust project.
            Supports: middleware, extractor, error-handler, auth-guard,
            rate-limiter, websocket, background-task.

            Args:
                description: Natural language description (e.g., "auth middleware")
            """
            return RustIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def rust_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a Rust symbol.
            - struct: checks impl blocks, users, derive compatibility
            - trait: checks all implementors
            - handler: checks route registration, middleware

            Args:
                file_path: Relative file path (e.g., "src/handlers/user.rs")
                symbol_name: Symbol to check (e.g., "User", "get_user", "AuthGuard")
            """
            return RustIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["RustPlugin"]
