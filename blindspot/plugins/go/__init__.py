"""Go plugin for Blindspot MCP.

Provides Go-specific intelligence tools for Gin/Echo/Chi/net-http:
- GORM struct relationship parsing
- Route mapping for multiple Go web frameworks
- Migration schema extraction (GORM + golang-migrate)
- Interface implementation tracking
- Middleware chain resolution
- Package dependency graphing
- Flow mapping (route -> middleware -> handler -> service -> repository)
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class GoPlugin(BlindspotPlugin):
    """Go framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "go"

    @property
    def framework(self) -> str:
        return "go"

    @property
    def description(self) -> str:
        return "Go framework intelligence -- Gin, Echo, Chi, GORM, interfaces, packages"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "internal/models",
            "controllers": "internal/handlers",
            "services": "internal/services",
            "middleware": "internal/middleware",
            "routes": "internal/routes",
            "tests": ".",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bfmt\.Println\s*\(",
                "severity": "warning",
                "message": "fmt.Println in production code -- use log package or structured logger",
                "file_types": ["go"],
            },
            {
                "pattern": r"\bpanic\s*\(",
                "severity": "error",
                "message": "panic() in library code -- return errors instead",
                "file_types": ["go"],
            },
            {
                "pattern": r"_\s*=\s*\w+\.\w+\(",
                "severity": "warning",
                "message": "Ignoring error with _ = -- handle or explicitly document why",
                "file_types": ["go"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Go-specific MCP tools."""
        from ...services.go_intelligence_service import GoIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_struct_relationships(ctx: Context, struct_name: str = None) -> dict[str, Any]:
            """
            Parse GORM struct tags and plain struct fields to extract relationships.
            Detects foreignKey, many2many, belongs_to, has_many, pointer/slice types.

            Args:
                struct_name: Optional struct name (e.g., "User"). If omitted, returns all.
            """
            return GoIntelligenceService(ctx).get_struct_relationships(struct_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse route definitions for Gin, Echo, Chi, and net/http.
            Extract routes, handlers, middleware groups.

            Args:
                filter_prefix: Optional URL prefix filter
            """
            return GoIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse GORM AutoMigrate calls and golang-migrate .sql files.
            Extract table/column information.

            Args:
                table_name: Optional table name filter
            """
            return GoIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace request flow: Route -> middleware -> handler -> service ->
            repository -> model.

            Args:
                entry_point: Handler function name or route path
                method: Optional HTTP method
            """
            return GoIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse router.Use() and group middleware in Gin/Echo/Chi.
            Return ordered middleware stack.

            Args:
                route_path: Optional route path to filter middleware for
            """
            return GoIntelligenceService(ctx).get_middleware_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_interface_implementations(ctx: Context, interface_name: str = None) -> dict[str, Any]:
            """
            Find all types implementing an interface by method set matching.
            Essential for Go's implicit interface satisfaction.

            Args:
                interface_name: Optional interface name. If omitted, returns all.
            """
            return GoIntelligenceService(ctx).get_interface_implementations(interface_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_go_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify endpoint: route registration, handler signature, middleware.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return GoIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Go project codebase.

            Args:
                pattern_type: One of: "naming", "error-handling", "concurrency", "testing"
            """
            return GoIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the Go project.

            Args:
                description: Description of what you're looking for
                            (e.g., "middleware", "repository", "worker")
            """
            return GoIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate Go project overview: packages, routes, structs, interfaces,
            middleware, go.mod dependencies.
            """
            return GoIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_go_dependency_graph(ctx: Context, package_name: str = None) -> dict[str, Any]:
            """
            Parse import statements across all .go files and build package
            dependency graph.

            Args:
                package_name: Optional package name to focus the graph on
            """
            return GoIntelligenceService(ctx).get_dependency_graph(package_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_go_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Context-aware checks before editing a Go symbol. Includes interface
            impact analysis and cross-package references.

            Args:
                file_path: Relative file path (e.g., "internal/handlers/user.go")
                symbol_name: Symbol to check (e.g., "CreateUser", "UserService")
            """
            return GoIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["GoPlugin"]
