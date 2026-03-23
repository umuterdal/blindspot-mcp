"""ASP.NET Core plugin for Blindspot MCP.

Provides ASP.NET Core-specific intelligence tools:
- Entity Framework Core relationship mapping
- Controller and Minimal API endpoint parsing
- EF Core migration schema extraction
- Razor view dependency analysis
- Middleware pipeline resolution
- Dependency injection graph
- Validation chain tracing (DataAnnotations)
- Flow mapping (endpoint -> service -> repository -> DbContext)
- Project convention detection
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class AspNetPlugin(BlindspotPlugin):
    """ASP.NET Core framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "aspnet"

    @property
    def framework(self) -> str:
        return "aspnet"

    @property
    def description(self) -> str:
        return "ASP.NET Core framework intelligence — EF Core, Razor, DI, middleware, controllers"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "controllers": "Controllers",
            "models": "Models",
            "services": "Services",
            "views": "Views",
            "middleware": "Middleware",
            "data": "Data",
            "migrations": "Migrations",
            "tests": "Tests",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"Console\.Write(Line)?\s*\(",
                "severity": "warning",
                "message": "Console.WriteLine in production code -- use ILogger<T> instead",
                "file_types": ["cs"],
            },
            {
                "pattern": r"\.Result\b",
                "severity": "error",
                "message": "Task.Result blocks the thread -- use await instead",
                "file_types": ["cs"],
            },
            {
                "pattern": r'"\s*\+\s*\w+.*(?:SELECT|INSERT|UPDATE|DELETE)',
                "severity": "error",
                "message": "String concatenation in SQL -- use parameterised queries to prevent SQL injection",
                "file_types": ["cs"],
            },
            {
                "pattern": r"\.Wait\s*\(\s*\)",
                "severity": "error",
                "message": "Task.Wait() blocks the thread -- use await instead",
                "file_types": ["cs"],
            },
            {
                "pattern": r"\.GetAwaiter\s*\(\s*\)\.GetResult\s*\(\s*\)",
                "severity": "error",
                "message": ".GetAwaiter().GetResult() blocks the thread -- use await",
                "file_types": ["cs"],
            },
            {
                "pattern": r"throw\s+new\s+Exception\s*\(",
                "severity": "warning",
                "message": "Throwing base Exception -- use a more specific exception type",
                "file_types": ["cs"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all ASP.NET Core-specific MCP tools."""
        from ...services.aspnet_intelligence_service import AspNetIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_entity_relationships(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Extract EF Core entity relationship map from DbContext and entity classes.
            Returns DbSet<T> mappings, navigation properties, [Key]/[ForeignKey]/[Required]
            attributes, and Fluent API relationships from OnModelCreating.

            Args:
                entity_name: Optional specific entity class name (e.g., "User").
                            If omitted, returns all entities.
            """
            return AspNetIntelligenceService(ctx).get_entity_relationships(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_endpoint_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse ASP.NET Core controllers ([HttpGet]/[HttpPost]/[Route]) and Minimal API
            (app.MapGet/MapPost) endpoints. Returns route, HTTP method, controller, action,
            parameters, and authorization info.

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/api/users")
            """
            return AspNetIntelligenceService(ctx).get_endpoint_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_schema_info(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Parse EF Core migrations to extract database schema information.
            Returns columns (with types, nullable, defaults), indexes, foreign keys,
            and migration history for each table.

            Args:
                entity_name: Optional entity or table name (e.g., "Users").
                            If omitted, returns schema for all tables.
            """
            return AspNetIntelligenceService(ctx).get_schema_info(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_view_dependencies(view_path: str, ctx: Context) -> dict[str, Any]:
            """
            Get all dependencies of a Razor view file (.cshtml/.razor).
            Returns: @model directive, layout, sections, partial views, tag helpers,
            view components, injected services, HTML helpers, and which controller renders it.

            Args:
                view_path: Relative path to the Razor view
                          (e.g., "Views/Users/Index.cshtml")
            """
            return AspNetIntelligenceService(ctx).get_view_dependencies(view_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete request flow from endpoint to data layer in ASP.NET Core.
            Traces: Endpoint -> Middleware -> Authorization -> Controller ->
            Service -> Repository -> DbContext -> Entity -> Response.

            Args:
                entry_point: Controller name ("UserController") or route path ("/api/users")
                method: Optional action method name. If omitted, returns flows for ALL actions.
            """
            return AspNetIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_middleware_chain(ctx: Context, endpoint: str = None) -> dict[str, Any]:
            """
            Parse ASP.NET Core middleware pipeline from Program.cs/Startup.cs.
            Returns app.UseXxx() order, authorization policies, and per-endpoint
            [Authorize]/[AllowAnonymous] attributes.

            Args:
                endpoint: Optional URL path to find endpoint-specific auth.
                         If omitted, returns the full middleware pipeline.
            """
            return AspNetIntelligenceService(ctx).get_middleware_chain(endpoint)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_dependency_injection(ctx: Context, service_name: str = None) -> dict[str, Any]:
            """
            Parse DI container registrations (AddScoped/AddTransient/AddSingleton) from
            Program.cs. Builds dependency graph from constructor injection and detects
            missing registrations.

            Args:
                service_name: Optional service/interface name (e.g., "IUserService").
                             If omitted, returns full DI graph.
            """
            return AspNetIntelligenceService(ctx).get_dependency_injection(service_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_validation_chain(controller: str, action: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the ASP.NET Core validation pipeline from [FromBody] DTO through
            DataAnnotations ([Required]/[StringLength]/[Range]) to ModelState.IsValid check.

            Args:
                controller: Controller class name (e.g., "UserController")
                action: Action method name (e.g., "Create")
            """
            return AspNetIntelligenceService(ctx).get_validation_chain(controller, action)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_aspnet_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify an ASP.NET Core endpoint: route exists, controller action handles method,
            authorization configured, model binding present. Returns pass/warning/fail.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE, PATCH)
                url: URL path (e.g., "/api/users/{id}")
            """
            return AspNetIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the ASP.NET Core project codebase.

            Args:
                pattern_type: One of: "naming" (class/method naming conventions),
                             "di" (DI registration patterns and lifetimes),
                             "middleware" (pipeline configuration),
                             "testing" (xUnit/NUnit/MSTest patterns),
                             "configuration" (appsettings.json, IOptions<T>)
            """
            return AspNetIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the ASP.NET Core project.
            Use before implementing new features to discover existing solutions.
            Supports: middleware, filter, health-check, background-service,
            signalr-hub, grpc-service, rate-limiting.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "health check", "background service", "rate limiting")
            """
            return AspNetIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_aspnet_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Get a high-level overview of the ASP.NET Core project.
            Returns: .csproj files, controllers, entities, services, repositories,
            DTOs, middleware pipeline, DI registration count, endpoint count, and test count.
            """
            return AspNetIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def aspnet_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in an ASP.NET Core project.
            Combines reference tracking with context-aware checks:
            - Controller: checks routes, views, DI
            - Entity/Model: checks migrations, DbContext, DTOs
            - Service: checks DI registration, consuming controllers
            - Middleware: checks pipeline order

            Args:
                file_path: Relative file path (e.g., "Controllers/UserController.cs")
                symbol_name: Symbol to check (e.g., "GetUser", "Email", "UserService")
            """
            return AspNetIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["AspNetPlugin"]
