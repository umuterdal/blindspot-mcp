"""Express.js plugin for Blindspot MCP.

Provides Express.js-specific intelligence tools:
- Mongoose/Sequelize/TypeORM model parsing
- Express route mapping
- Migration schema extraction
- EJS/Handlebars/Pug template dependency analysis
- Middleware chain resolution
- Validation chain tracing
- Flow mapping (route -> middleware -> handler -> service -> model)
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class ExpressPlugin(BlindspotPlugin):
    """Express.js framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "express"

    @property
    def framework(self) -> str:
        return "express"

    @property
    def description(self) -> str:
        return "Express.js framework intelligence -- Mongoose, Sequelize, TypeORM, EJS, routes, middleware"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "src/models",
            "controllers": "src/controllers",
            "routes": "src/routes",
            "middleware": "src/middleware",
            "services": "src/services",
            "views": "src/views",
            "tests": "tests",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bconsole\.log\s*\(",
                "severity": "warning",
                "message": "console.log in production code -- use a proper logger",
                "file_types": ["js", "ts"],
            },
            {
                "pattern": r"\bvar\s+",
                "severity": "warning",
                "message": "Use const/let instead of var",
                "file_types": ["js", "ts"],
            },
            {
                "pattern": r"function\s*\([^)]*\)\s*\{[^}]*function\s*\([^)]*\)\s*\{[^}]*function\s*\(",
                "severity": "error",
                "message": "Callback hell -- nested callbacks > 3 levels deep",
                "file_types": ["js"],
            },
            {
                "pattern": r"\brequire\s*\([^)]+\)",
                "severity": "info",
                "message": "require() inside function body -- prefer top-level imports",
                "file_types": ["js"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Express.js-specific MCP tools."""
        from ...services.express_intelligence_service import ExpressIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_model_schema(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Parse Mongoose schemas, Sequelize define(), and TypeORM @Entity/@Column decorators.
            Extract fields, types, relations, indexes, validators.

            Args:
                model_name: Optional model name (e.g., "User"). If omitted, returns all models.
            """
            return ExpressIntelligenceService(ctx).get_model_schema(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse Express router files: router.get/post/put/delete(), app.use(), route grouping.
            Returns path, handler, middleware chain.

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/api/users")
            """
            return ExpressIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Sequelize migrations (createTable, addColumn) or Knex migrations
            (table.string/integer/etc). Return database schema.

            Args:
                table_name: Optional table name filter. If omitted, returns all tables.
            """
            return ExpressIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_template_dependencies(template_path: str, ctx: Context) -> dict[str, Any]:
            """
            Parse EJS, Handlebars, and Pug templates. Return template dependency tree
            including includes, partials, extends, and blocks.

            Args:
                template_path: Relative path to the template file
            """
            return ExpressIntelligenceService(ctx).get_template_dependencies(template_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace request flow: Route -> middleware -> handler -> service -> model -> response.

            Args:
                entry_point: Controller/handler name or route path
                method: Optional HTTP method (GET, POST, etc.)
            """
            return ExpressIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse app.use() and router-level middleware. Extract ordered middleware
            stack and which paths they apply to.

            Args:
                route_path: Optional route path to filter middleware for
            """
            return ExpressIntelligenceService(ctx).get_middleware_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_validation_chain(route_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace validation: express-validator/Joi/Zod schema -> middleware -> handler.
            Detect mismatches between validated and used fields.

            Args:
                route_path: Route path to trace validation for
            """
            return ExpressIntelligenceService(ctx).get_validation_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_express_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify endpoint: check route exists, handler defined, middleware chain,
            and error handling.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return ExpressIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Express.js project codebase.

            Args:
                pattern_type: One of: "naming", "middleware", "error-handling", "auth", "testing"
            """
            return ExpressIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the Express.js project.

            Args:
                description: Description of what you're looking for
                            (e.g., "file upload", "pagination", "rate limiting")
            """
            return ExpressIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_express_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate Express.js project overview: routes, models, middleware,
            env vars, scripts, and dependencies.
            """
            return ExpressIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_express_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Context-aware checks before editing a symbol in an Express.js project.
            Combines ripple effect analysis with Express-specific checks.

            Args:
                file_path: Relative file path (e.g., "src/controllers/userController.js")
                symbol_name: Symbol to check (e.g., "createUser", "authMiddleware")
            """
            return ExpressIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["ExpressPlugin"]
