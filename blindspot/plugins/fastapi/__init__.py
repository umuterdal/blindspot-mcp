"""FastAPI plugin for Blindspot MCP.

Provides FastAPI/Python-specific intelligence tools:
- SQLAlchemy model + Pydantic schema parsing
- FastAPI/Flask route mapping
- Alembic migration schema extraction
- Dependency injection chain tracing
- Middleware resolution
- Validation chain tracing (Pydantic request -> handler -> response)
- Flow mapping (route -> Depends() -> handler -> service -> CRUD -> SQLAlchemy)
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class FastAPIPlugin(BlindspotPlugin):
    """FastAPI framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "fastapi"

    @property
    def framework(self) -> str:
        return "fastapi"

    @property
    def description(self) -> str:
        return "FastAPI framework intelligence -- SQLAlchemy, Pydantic, Alembic, Depends, async"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "app/models",
            "controllers": "app/routers",
            "services": "app/services",
            "middleware": "app/middleware",
            "tests": "tests",
            "schemas": "app/schemas",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bprint\s*\(",
                "severity": "warning",
                "message": "print() in production code -- use logging module instead",
                "file_types": ["py"],
            },
            {
                "pattern": r"time\.sleep\s*\(",
                "severity": "error",
                "message": "Blocking time.sleep() in async handler -- use asyncio.sleep() instead",
                "file_types": ["py"],
            },
            {
                "pattern": r"from\s+\w+\s+import\s+\*",
                "severity": "warning",
                "message": "Wildcard import (import *) -- use explicit imports",
                "file_types": ["py"],
            },
            {
                "pattern": r"""['\"][A-Za-z0-9+/=]{20,}['\"]""",
                "severity": "error",
                "message": "Potential hardcoded secret/token -- use environment variables",
                "file_types": ["py"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all FastAPI-specific MCP tools."""
        from ...services.fastapi_intelligence_service import FastAPIIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_model_schema(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Parse SQLAlchemy models (Column, relationship(), ForeignKey) and
            Pydantic schemas (BaseModel fields). Return model -> schema mapping.

            Args:
                model_name: Optional model name. If omitted, returns all models and schemas.
            """
            return FastAPIIntelligenceService(ctx).get_model_schema(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse FastAPI decorators: @app.get/post/put/delete, @router.get/post,
            APIRouter(prefix=). Also Flask @app.route. Extract paths, methods,
            dependencies, response_model.

            Args:
                filter_prefix: Optional URL prefix filter
            """
            return FastAPIIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Alembic migrations (op.create_table, op.add_column, etc).
            Return database schema.

            Args:
                table_name: Optional table name filter
            """
            return FastAPIIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace: Route -> Depends() -> handler -> service -> CRUD ->
            SQLAlchemy query -> response.

            Args:
                entry_point: Handler name or route path
                method: Optional HTTP method
            """
            return FastAPIIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_middleware_chain(ctx: Context, route_path: str = None) -> dict[str, Any]:
            """
            Parse app.add_middleware(), @app.middleware("http"), and
            BaseHTTPMiddleware subclasses.

            Args:
                route_path: Optional route path for context
            """
            return FastAPIIntelligenceService(ctx).get_middleware_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_validation_chain(route_path: str, ctx: Context) -> dict[str, Any]:
            """
            Trace: Pydantic request model -> FastAPI handler param -> response model.
            Detect field mismatches between request and response schemas.

            Args:
                route_path: Route path to trace validation for
            """
            return FastAPIIntelligenceService(ctx).get_validation_chain(route_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_fastapi_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify endpoint: route exists, handler params, auth dependencies,
            response model.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users")
            """
            return FastAPIIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the FastAPI project.

            Args:
                pattern_type: One of: "naming", "dependency-injection", "orm", "testing", "async"
            """
            return FastAPIIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the FastAPI project.

            Args:
                description: Description of what you're looking for
                            (e.g., "auth dependency", "file upload", "background task")
            """
            return FastAPIIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate FastAPI project overview: routers, models, schemas,
            dependencies, middleware, background tasks.
            """
            return FastAPIIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_fastapi_dependency_injection(ctx: Context, router_path: str = None) -> dict[str, Any]:
            """
            Parse Depends() chains and build dependency graph.
            Resolves nested dependency chains.

            Args:
                router_path: Optional router file path to filter
            """
            return FastAPIIntelligenceService(ctx).get_dependency_injection(router_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_fastapi_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Context-aware checks before editing a FastAPI symbol. Includes
            dependency provider impact and Pydantic schema usage analysis.

            Args:
                file_path: Relative file path (e.g., "app/routers/users.py")
                symbol_name: Symbol to check (e.g., "create_user", "UserSchema")
            """
            return FastAPIIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["FastAPIPlugin"]
