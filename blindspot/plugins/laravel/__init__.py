"""Laravel plugin for Blindspot MCP.

Provides Laravel-specific intelligence tools:
- Eloquent relationship mapping
- Route parsing
- Migration schema extraction
- Blade template dependency analysis
- Cache invalidation mapping
- Validation chain tracing
- Middleware stack resolution
- Flow mapping (route → controller → side effects)
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class LaravelPlugin(BlindspotPlugin):
    """Laravel framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "laravel"

    @property
    def framework(self) -> str:
        return "laravel"

    @property
    def description(self) -> str:
        return "Laravel framework intelligence — Eloquent, Blade, routes, migrations, cache"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "app/Models",
            "controllers": "app/Http/Controllers",
            "views": "resources/views",
            "services": "app/Services",
            "migrations": "database/migrations",
            "routes": "routes",
            "middleware": "app/Http/Middleware",
            "requests": "app/Http/Requests",
            "policies": "app/Policies",
            "events": "app/Events",
            "listeners": "app/Listeners",
            "jobs": "app/Jobs",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bdd\s*\(",
                "severity": "error",
                "message": "Debug function dd() in code — remove before commit",
                "file_types": ["php"],
            },
            {
                "pattern": r"\bdump\s*\(",
                "severity": "error",
                "message": "Debug function dump() in code — remove before commit",
                "file_types": ["php"],
            },
            {
                "pattern": r"\$guarded\s*=\s*\[\s*\]",
                "severity": "error",
                "message": "Empty $guarded array — use $fillable instead",
                "file_types": ["php"],
            },
            {
                "pattern": r"DB::raw\([^)]*(?<!\?)[^)]*\)",
                "severity": "warning",
                "message": "DB::raw() without parameter binding — SQL injection risk",
                "file_types": ["php"],
            },
            {
                "pattern": r"Route::\w+\(\s*['\"][^'\"]+['\"]\s*,\s*function",
                "severity": "error",
                "message": "Closure route — use controller reference for route caching",
                "file_types": ["php"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Laravel-specific MCP tools."""
        from ...services.laravel_intelligence_service import LaravelIntelligenceService
        from ...services.laravel_validation_service import LaravelValidationService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_laravel_relationships(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Extract Eloquent relationship map from model files.
            Returns relationships (hasOne, hasMany, belongsTo, etc.), traits, and fillable fields.

            Args:
                model_name: Optional specific model name (e.g., "User"). If omitted, returns all models.
            """
            return LaravelIntelligenceService(ctx).get_laravel_relationships(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse routes/web.php and return route -> controller -> method mapping.
            Useful for understanding which controller handles which URL.

            Args:
                filter_prefix: Optional route name prefix filter (e.g., "api.users")
            """
            return LaravelIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_blade_dependencies(view_path: str, ctx: Context) -> dict[str, Any]:
            """
            Get all dependencies of a Blade view file.
            Returns: parent layout, included partials, components, referenced routes,
            Alpine.js components, sections, pushed stacks, and which controller renders it.

            Args:
                view_path: Relative path to the view (e.g., "resources/views/users/index.blade.php")
            """
            return LaravelIntelligenceService(ctx).get_blade_dependencies(view_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Laravel migration files and extract database schema information.
            Returns columns (with types, nullable, defaults), indexes, and foreign keys.

            Args:
                table_name: Optional specific table name (e.g., "users").
                            If omitted, returns schema for all tables.
            """
            return LaravelIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete business flow from route to all side effects.
            "What happens when a user hits this endpoint?" — answered in one call.

            Traces: Route -> Middleware -> Validation -> Model operations -> Service calls ->
            Events dispatched -> Cache operations -> View rendered/Redirect

            Args:
                entry_point: Controller name ("UserController") or route name ("api.users.store")
                method: Optional method name. If omitted with controller name,
                       returns flows for ALL public methods.
            """
            return LaravelIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_cache_map(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Map all cache invalidation from Laravel Model booted() callbacks.
            Builds forward_map (model -> cache keys it invalidates) and reverse_map
            (cache key -> which models invalidate it + which files read it).

            Args:
                model_name: Optional specific model name (e.g., "Category").
                            If omitted, scans ALL models.
            """
            return LaravelValidationService(ctx).get_cache_map(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_view_data_flow(view_path: str, ctx: Context) -> dict[str, Any]:
            """
            Map controller data -> Blade variable usage and flag mismatches.
            Finds which controllers render a view, what variables they pass,
            and which variables the Blade template actually uses.

            Args:
                view_path: Relative path to the Blade view
            """
            return LaravelValidationService(ctx).get_view_data_flow(view_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_validation_chain(controller: str, method: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the full form -> validation pipeline and flag mismatches.
            Cross-references FormRequest rules with Blade form fields.

            Args:
                controller: Controller name (e.g., "UserController")
                method: Method name (e.g., "store")
            """
            return LaravelValidationService(ctx).get_validation_chain(controller, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_middleware_chain(route_name: str, ctx: Context) -> dict[str, Any]:
            """
            Show full middleware stack for a route and group routes sharing throttle counters.

            Args:
                route_name: Route name (e.g., "api.users.store") or URL path (e.g., "/api/users")
            """
            return LaravelValidationService(ctx).get_middleware_chain(route_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify an endpoint: route registration, controller syntax, middleware,
            validation rules, and potential failure points.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return LaravelValidationService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Laravel project codebase.

            Args:
                pattern_type: One of: "naming", "validation", "cache", "component", "error_handling", "route"
            """
            return LaravelValidationService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the project.
            Use before implementing new features to discover existing solutions.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "modal overlay", "file upload", "form validation")
            """
            return LaravelValidationService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol. Combines ripple effect
            with contextual deep checks based on file type.

            Args:
                file_path: Relative file path (e.g., "app/Models/User.php")
                symbol_name: Symbol to check (e.g., "scopeActive", "store", "rules")
            """
            return LaravelValidationService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["LaravelPlugin"]
