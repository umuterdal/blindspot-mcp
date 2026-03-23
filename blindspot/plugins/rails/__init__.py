"""Rails plugin for Blindspot MCP.

Provides Ruby on Rails-specific intelligence tools:
- ActiveRecord relationship mapping
- Route parsing from config/routes.rb
- Migration schema extraction
- ERB/HAML template dependency analysis
- Cache invalidation mapping
- Rack middleware resolution
- Validation chain tracing (strong params -> model validates -> form fields)
- Flow mapping (route -> before_action -> controller#action -> model -> view)
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class RailsPlugin(BlindspotPlugin):
    """Ruby on Rails framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "rails"

    @property
    def framework(self) -> str:
        return "rails"

    @property
    def description(self) -> str:
        return "Rails framework intelligence -- ActiveRecord, ERB, routes, migrations, cache, jobs"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "app/models",
            "controllers": "app/controllers",
            "views": "app/views",
            "services": "app/services",
            "jobs": "app/jobs",
            "mailers": "app/mailers",
            "migrations": "db/migrate",
            "routes": "config",
            "tests": "test",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bputs\b\s",
                "severity": "warning",
                "message": "puts in production code -- use Rails.logger instead",
                "file_types": ["rb"],
            },
            {
                "pattern": r"\bp\b\s",
                "severity": "warning",
                "message": "p() debug output in code -- use Rails.logger instead",
                "file_types": ["rb"],
            },
            {
                "pattern": r"\.all\b(?!\s*\.\s*(?:includes|eager_load|preload|joins|where|select|order|limit))",
                "severity": "warning",
                "message": "Potential N+1 query -- consider adding .includes() or .eager_load()",
                "file_types": ["rb"],
            },
            {
                "pattern": r"\.save!",
                "severity": "info",
                "message": "save! without rescue -- consider handling ActiveRecord::RecordInvalid",
                "file_types": ["rb"],
            },
            {
                "pattern": r"binding\.pry",
                "severity": "error",
                "message": "binding.pry debugger left in code -- remove before commit",
                "file_types": ["rb"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Rails-specific MCP tools."""
        from ...services.rails_intelligence_service import RailsIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_model_relationships(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Parse ActiveRecord relationships: has_many, belongs_to, has_one,
            has_and_belongs_to_many, has_many :through. Also: validates, scope,
            enum, callbacks.

            Args:
                model_name: Optional model name (e.g., "User"). If omitted, returns all.
            """
            return RailsIntelligenceService(ctx).get_model_relationships(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse config/routes.rb: resources, get/post/put/delete, namespace,
            scope, mount. Return route -> controller#action mapping.

            Args:
                filter_prefix: Optional URL prefix filter
            """
            return RailsIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse db/migrate/*.rb: create_table, add_column, add_index,
            add_reference. Return database schema.

            Args:
                table_name: Optional table name filter
            """
            return RailsIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_template_dependencies(template_path: str, ctx: Context) -> dict[str, Any]:
            """
            Parse ERB (render partial:, yield, content_for) and HAML (= render)
            templates. Return dependency tree.

            Args:
                template_path: Relative path to the template file
            """
            return RailsIntelligenceService(ctx).get_template_dependencies(template_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace: Route -> before_action -> controller#action -> model -> view/JSON.

            Args:
                entry_point: Controller name or route path
                method: Optional HTTP method or action name
            """
            return RailsIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_cache_map(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Find Rails.cache.fetch/read/write, fragment caching, Russian doll caching,
            and after_commit -> cache expiry patterns.

            Args:
                model_name: Optional model name to filter
            """
            return RailsIntelligenceService(ctx).get_cache_map(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_middleware_chain(ctx: Context, route_name: str = None) -> dict[str, Any]:
            """
            Parse Rack middleware in config/application.rb and
            config.middleware.use/insert.

            Args:
                route_name: Optional route name for context
            """
            return RailsIntelligenceService(ctx).get_middleware_chain(route_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_validation_chain(controller: str, action: str, ctx: Context) -> dict[str, Any]:
            """
            Trace: strong_parameters (params.require.permit) -> model validates ->
            form_for/form_with fields. Detect mismatches.

            Args:
                controller: Controller name (e.g., "UsersController")
                action: Action name (e.g., "create")
            """
            return RailsIntelligenceService(ctx).get_validation_chain(controller, action)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_rails_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify endpoint: route exists, controller#action defined,
            before_actions, strong params.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/users")
            """
            return RailsIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Rails project codebase.

            Args:
                pattern_type: One of: "naming", "testing", "concerns", "service-objects", "jobs"
            """
            return RailsIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the Rails project.

            Args:
                description: Description of what you're looking for
                            (e.g., "service object", "form object", "mailer")
            """
            return RailsIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rails_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate Rails project overview: models, controllers, routes, gems,
            migrations, jobs, mailers, channels.
            """
            return RailsIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_rails_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Context-aware checks before editing a Rails symbol. Includes
            ActiveRecord cascade analysis and test impact.

            Args:
                file_path: Relative file path (e.g., "app/models/user.rb")
                symbol_name: Symbol to check (e.g., "has_many", "before_save")
            """
            return RailsIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["RailsPlugin"]
