"""Phoenix plugin for Blindspot MCP.

Provides Phoenix/Elixir-specific intelligence tools:
- Ecto schema relationship mapping (has_many, belongs_to, changeset)
- Router pipeline and route parsing (scope, pipe_through, resources, live)
- Ecto migration schema extraction
- HEEx template dependency analysis
- LiveView callback and event mapping
- Phoenix context (bounded context) mapping
- Plug pipeline resolution
- Flow mapping (route -> pipeline -> controller/LiveView -> context -> Ecto)
- Project convention detection
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class PhoenixPlugin(BlindspotPlugin):
    """Phoenix framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "phoenix"

    @property
    def framework(self) -> str:
        return "phoenix"

    @property
    def description(self) -> str:
        return "Phoenix framework intelligence -- Ecto, LiveView, contexts, plugs, HEEx templates"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "controllers": "lib/app_web/controllers",
            "live": "lib/app_web/live",
            "views": "lib/app_web/views",
            "templates": "lib/app_web/templates",
            "contexts": "lib/app",
            "schemas": "lib/app",
            "channels": "lib/app_web/channels",
            "migrations": "priv/repo/migrations",
            "routes": "lib/app_web",
            "tests": "test",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bIO\.inspect\b",
                "severity": "warning",
                "message": "IO.inspect in production code -- use Logger instead",
                "file_types": ["ex", "exs"],
            },
            {
                "pattern": r"\bRepo\.\w+\s*\(",
                "severity": "warning",
                "message": "Direct Repo call -- use context module for business logic encapsulation",
                "file_types": ["ex"],
            },
            {
                "pattern": r"<<\s*\w+\s*>>",
                "severity": "info",
                "message": "Binary pattern match without size -- may match unexpected data",
                "file_types": ["ex", "exs"],
            },
            {
                "pattern": r"\bIO\.puts\b",
                "severity": "warning",
                "message": "IO.puts in production code -- use Logger instead",
                "file_types": ["ex", "exs"],
            },
            {
                "pattern": r"String\.to_atom\s*\(",
                "severity": "error",
                "message": "String.to_atom/1 with user input -- atom table is not garbage collected, use to_existing_atom/1",
                "file_types": ["ex", "exs"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Phoenix-specific MCP tools."""
        from ...services.phoenix_intelligence_service import PhoenixIntelligenceService
        from ...utils import handle_mcp_tool_errors

        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_schema_relationships(ctx: Context, schema_name: str = None) -> dict[str, Any]:
            """
            Extract Ecto schema relationships: has_many, belongs_to, has_one,
            many_to_many, field types, virtual fields, and changeset validations.

            Args:
                schema_name: Optional schema module name (e.g., "User").
                            If omitted, returns all schemas.
            """
            return PhoenixIntelligenceService(ctx).get_schema_relationships(schema_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse Phoenix router.ex: pipe_through, scope, get/post/put/delete,
            resources, and live routes (live "/path", LiveView).

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/api")
            """
            return PhoenixIntelligenceService(ctx).get_route_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Ecto migration files: create table, add/modify columns,
            create index, references (foreign keys).

            Args:
                table_name: Optional table name (e.g., "users").
                           If omitted, returns schema for all tables.
            """
            return PhoenixIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_template_dependencies(template_path: str, ctx: Context) -> dict[str, Any]:
            """
            Parse HEEx template dependencies: <.component>, slots, live_component,
            assigns, links, forms, conditional blocks, and comprehensions.

            Args:
                template_path: Relative path to the HEEx template
                              (e.g., "lib/my_app_web/live/user_live/index.html.heex")
            """
            return PhoenixIntelligenceService(ctx).get_template_dependencies(template_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete Phoenix request flow.
            Traces: Route -> Pipeline -> Plug chain -> Controller/LiveView ->
            Context/Assigns -> Ecto query -> Template render.

            Args:
                entry_point: Controller or LiveView module name (e.g., "UserController")
                method: Optional function name (e.g., "index", "handle_event").
                       If omitted, returns flows for all public functions.
            """
            return PhoenixIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_pipeline_chain(ctx: Context, pipeline_name: str = None) -> dict[str, Any]:
            """
            Parse Phoenix router.ex pipelines: plug sequence, authentication plugs,
            CSRF protection. Also finds custom Plug implementations.

            Args:
                pipeline_name: Optional pipeline name (e.g., "browser", "api").
                              If omitted, returns all pipelines.
            """
            return PhoenixIntelligenceService(ctx).get_pipeline_chain(pipeline_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_liveview_map(ctx: Context, liveview_name: str = None) -> dict[str, Any]:
            """
            Parse Phoenix LiveView modules: mount, handle_event, handle_info,
            handle_params, assigns. Maps LiveView -> component -> event chain.

            Args:
                liveview_name: Optional LiveView module name (e.g., "UserLive").
                              If omitted, returns all LiveViews.
            """
            return PhoenixIntelligenceService(ctx).get_liveview_map(liveview_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_context_map(ctx: Context, context_name: str = None) -> dict[str, Any]:
            """
            Parse Phoenix contexts (bounded contexts): public functions, Ecto queries,
            changesets. Maps context -> schema -> controller/LiveView usage.

            Args:
                context_name: Optional context module name (e.g., "Accounts").
                             If omitted, returns all contexts.
            """
            return PhoenixIntelligenceService(ctx).get_context_map(context_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_phoenix_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Phoenix endpoint: route exists, controller/LiveView handles it,
            pipeline configured, auth plugs present.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE) or "LIVE"
                url: URL path (e.g., "/users/:id")
            """
            return PhoenixIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Phoenix project codebase.

            Args:
                pattern_type: One of: "naming" (module/function naming patterns),
                             "context" (context boundary analysis),
                             "testing" (ExUnit test patterns),
                             "liveview" (LiveView callback patterns),
                             "ecto" (Ecto query and transaction patterns)
            """
            return PhoenixIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Get a high-level overview of the Phoenix project.
            Returns: mix.exs info, contexts, schemas, controllers, LiveViews,
            channels, plugs, routes, pipelines, and test count.
            """
            return PhoenixIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_phoenix_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns in the Phoenix project.
            Supports: liveview, channel, genserver, task, plug, live-component,
            form-component, table-component.

            Args:
                description: Natural language description (e.g., "liveview", "genserver")
            """
            return PhoenixIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def phoenix_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a Phoenix project.
            - Controller: checks routes, templates
            - LiveView: checks routes, JS hooks
            - Schema: checks migrations, context functions
            - Context: checks controllers, LiveViews using it

            Args:
                file_path: Relative file path (e.g., "lib/my_app/accounts.ex")
                symbol_name: Symbol to check (e.g., "create_user", "User", "handle_event")
            """
            return PhoenixIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["PhoenixPlugin"]
