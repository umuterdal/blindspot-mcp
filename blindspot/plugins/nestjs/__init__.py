"""NestJS plugin for Blindspot MCP.

Provides NestJS-specific intelligence tools:
- Module dependency graph (@Module imports/exports/providers)
- Decorator-based endpoint parsing (@Get/@Post on @Controller)
- TypeORM and Prisma entity relationship mapping
- Guard / Pipe / Interceptor resolution
- Constructor-based dependency injection graph
- Validation chain tracing (class-validator + ValidationPipe)
- Flow mapping (route -> guard -> pipe -> controller -> service)
- Project convention detection
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class NestJSPlugin(BlindspotPlugin):
    """NestJS framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "nestjs"

    @property
    def framework(self) -> str:
        return "nestjs"

    @property
    def description(self) -> str:
        return "NestJS framework intelligence -- modules, guards, pipes, TypeORM, Prisma, DI"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "modules": "src",
            "controllers": "src",
            "services": "src",
            "entities": "src",
            "guards": "src/guards",
            "pipes": "src/pipes",
            "interceptors": "src/interceptors",
            "middleware": "src/middleware",
            "dtos": "src",
            "tests": "test",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bconsole\.(log|warn|error|debug|info)\s*\(",
                "severity": "warning",
                "message": "console.log in production code -- use NestJS Logger service instead",
                "file_types": ["ts"],
            },
            {
                "pattern": r":\s*any\b",
                "severity": "warning",
                "message": "Using 'any' type -- prefer explicit typing for type safety",
                "file_types": ["ts"],
            },
            {
                "pattern": r"@Inject\s*\([^)]*\)\s*(?!.*@Injectable)",
                "severity": "warning",
                "message": "@Inject without @Injectable on class -- may cause DI errors",
                "file_types": ["ts"],
            },
            {
                "pattern": r"forwardRef\s*\(\s*\(\)\s*=>",
                "severity": "warning",
                "message": "forwardRef detected -- possible circular dependency",
                "file_types": ["ts"],
            },
            {
                "pattern": r"\.then\s*\(\s*\(",
                "severity": "warning",
                "message": ".then() callback -- prefer async/await for readability",
                "file_types": ["ts"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all NestJS-specific MCP tools."""
        from ...services.nestjs_intelligence_service import NestJSIntelligenceService
        from ...utils import handle_mcp_tool_errors

        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_module_map(ctx: Context, module_name: str = None) -> dict[str, Any]:
            """
            Parse NestJS @Module() decorators and build module dependency graph.
            Returns imports, controllers, providers, and exports for each module.

            Args:
                module_name: Optional specific module class name (e.g., "UsersModule").
                            If omitted, returns all modules.
            """
            return NestJSIntelligenceService(ctx).get_module_map(module_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_endpoint_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse NestJS @Controller + @Get/@Post/@Put/@Delete/@Patch decorators.
            Returns route, HTTP method, controller, handler, guards, interceptors, and pipes.

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/users")
            """
            return NestJSIntelligenceService(ctx).get_endpoint_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_entity_relationships(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Extract TypeORM entity relationships (@OneToMany, @ManyToOne, @ManyToMany,
            @OneToOne) and Prisma schema models. Returns columns, relationships, and
            primary keys.

            Args:
                entity_name: Optional specific entity class name (e.g., "User").
                            If omitted, returns all entities.
            """
            return NestJSIntelligenceService(ctx).get_entity_relationships(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete NestJS request flow from route to data layer.
            Traces: Route -> Guard -> Interceptor -> Pipe -> Controller ->
            Service -> Repository -> Entity -> Response.

            Args:
                entry_point: Controller class name ("UsersController") or route path
                method: Optional handler method name. If omitted, returns all handlers.
            """
            return NestJSIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse NestJS middleware: global app.use(), module-level
            consumer.apply().forRoutes(), and custom NestMiddleware classes.

            Args:
                route_path: Optional route path to find route-specific middleware.
                           If omitted, returns all middleware.
            """
            return NestJSIntelligenceService(ctx).get_middleware_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_guard_map(ctx: Context, guard_name: str = None) -> dict[str, Any]:
            """
            Parse NestJS guards: @UseGuards decorator usage and CanActivate implementations.
            Maps guard definitions to endpoint usage across the project.

            Args:
                guard_name: Optional guard class name (e.g., "JwtAuthGuard").
                           If omitted, returns all guards.
            """
            return NestJSIntelligenceService(ctx).get_guard_map(guard_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_pipe_map(ctx: Context, pipe_name: str = None) -> dict[str, Any]:
            """
            Parse NestJS pipes: @UsePipes, ValidationPipe, and custom PipeTransform
            implementations. Maps pipe definitions to endpoint and global usage.

            Args:
                pipe_name: Optional pipe class name (e.g., "ParseIntPipe").
                          If omitted, returns all pipes.
            """
            return NestJSIntelligenceService(ctx).get_pipe_map(pipe_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_dependency_injection(ctx: Context, provider_name: str = None) -> dict[str, Any]:
            """
            Parse @Injectable + constructor injection to build NestJS DI graph.
            Detects circular dependencies and maps provider scopes.

            Args:
                provider_name: Optional provider class name (e.g., "UsersService").
                              If omitted, returns full DI graph.
            """
            return NestJSIntelligenceService(ctx).get_dependency_injection(provider_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_validation_chain(controller: str, method: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the NestJS validation pipeline: @Body() DTO -> class-validator decorators
            (@IsString, @IsEmail, @IsNotEmpty) -> ValidationPipe -> handler.

            Args:
                controller: Controller class name (e.g., "UsersController")
                method: Handler method name (e.g., "create")
            """
            return NestJSIntelligenceService(ctx).get_validation_chain(controller, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_nestjs_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a NestJS endpoint: route registration, method decorator, guards,
            pipes, DTOs. Returns pass/warning/fail status.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE, PATCH)
                url: URL path (e.g., "/users/:id")
            """
            return NestJSIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the NestJS project codebase.

            Args:
                pattern_type: One of: "naming" (class/file naming patterns),
                             "modules" (module organization and structure),
                             "testing" (jest mocking and testing patterns),
                             "interceptors" (interceptor implementations),
                             "exception-filters" (exception filter implementations)
            """
            return NestJSIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Get a high-level overview of the NestJS project.
            Returns: modules, controllers, services, entities, guards, pipes,
            interceptors, DTOs, endpoint count, test count, and package info.
            """
            return NestJSIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_nestjs_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the NestJS project.
            Supports: guard, interceptor, pipe, filter, gateway (websocket),
            microservice, scheduler, queue.

            Args:
                description: Natural language description (e.g., "websocket", "auth guard")
            """
            return NestJSIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def nestjs_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a NestJS project.
            - Controller: checks routes, module registration
            - Service: checks DI, consuming controllers
            - Entity: checks migrations, repositories
            - Guard: checks all endpoints using it
            - Module: checks importing modules

            Args:
                file_path: Relative file path (e.g., "src/users/users.controller.ts")
                symbol_name: Symbol to check (e.g., "UsersService", "findAll")
            """
            return NestJSIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["NestJSPlugin"]
