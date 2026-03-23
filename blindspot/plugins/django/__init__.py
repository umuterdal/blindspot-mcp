"""Django plugin for Blindspot MCP.

Provides Django-specific intelligence tools:
- Model relationship mapping
- URL pattern parsing
- Migration schema extraction
- Template dependency analysis
- Request flow tracing
- Cache invalidation mapping
- Middleware chain resolution
- Form validation chain tracing
- Endpoint verification
- Project convention extraction
- Similar pattern discovery
- Project snapshot overview
- DRF serializer chain tracing
- Pre-edit impact checking
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class DjangoPlugin(BlindspotPlugin):
    """Django framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "django"

    @property
    def framework(self) -> str:
        return "django"

    @property
    def description(self) -> str:
        return "Django framework intelligence — models, URLs, templates, DRF, migrations, cache"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "models": "*/models.py",
            "views": "*/views.py",
            "templates": "*/templates",
            "forms": "*/forms.py",
            "serializers": "*/serializers.py",
            "migrations": "*/migrations",
            "admin": "*/admin.py",
            "urls": "*/urls.py",
            "middleware": "*/middleware.py",
            "signals": "*/signals.py",
            "tasks": "*/tasks.py",
            "management_commands": "*/management/commands",
            "templatetags": "*/templatetags",
            "tests": "*/tests.py",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bprint\s*\(",
                "severity": "warning",
                "message": "print() in code — use logging instead",
                "file_types": ["py"],
            },
            {
                "pattern": r"\bbreakpoint\s*\(",
                "severity": "error",
                "message": "breakpoint() in code — remove before commit",
                "file_types": ["py"],
            },
            {
                "pattern": r"\bimport\s+pdb\b",
                "severity": "error",
                "message": "pdb import in code — remove before commit",
                "file_types": ["py"],
            },
            {
                "pattern": r"DEBUG\s*=\s*True",
                "severity": "warning",
                "message": "DEBUG = True — ensure this is not in production settings",
                "file_types": ["py"],
            },
            {
                "pattern": r"\.raw\s*\(\s*['\"]",
                "severity": "warning",
                "message": "Raw SQL query — verify parameter binding to prevent SQL injection",
                "file_types": ["py"],
            },
            {
                "pattern": r"\$guarded\s*=\s*\[\s*\]",
                "severity": "error",
                "message": "Empty guarded — potential mass assignment vulnerability",
                "file_types": ["py"],
            },
            {
                "pattern": r"\.extra\s*\(",
                "severity": "warning",
                "message": ".extra() is deprecated — use annotate/aggregate instead",
                "file_types": ["py"],
            },
            {
                "pattern": r"@csrf_exempt",
                "severity": "warning",
                "message": "@csrf_exempt disables CSRF protection — verify this is intentional",
                "file_types": ["py"],
            },
            {
                "pattern": r"ALLOWED_HOSTS\s*=\s*\[\s*['\"]?\*['\"]?\s*\]",
                "severity": "error",
                "message": "ALLOWED_HOSTS = ['*'] — restrict in production",
                "file_types": ["py"],
            },
            {
                "pattern": r"SECRET_KEY\s*=\s*['\"][^'\"]+['\"]",
                "severity": "error",
                "message": "Hardcoded SECRET_KEY — use environment variable",
                "file_types": ["py"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Django-specific MCP tools."""
        from ...services.django_intelligence_service import DjangoIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_relationships(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Extract Django model relationship map from models.py files.
            Returns relationships (ForeignKey, ManyToMany, OneToOne), fields, Meta class,
            managers, properties, and methods.

            Args:
                model_name: Optional specific model name (e.g., "User"). If omitted, returns all models.
            """
            return DjangoIntelligenceService(ctx).get_model_relationships(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_url_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse Django urls.py files and return URL -> view mapping.
            Handles path(), re_path(), include(), DRF router registrations, and namespaces.

            Args:
                filter_prefix: Optional URL name or path prefix filter (e.g., "api", "admin")
            """
            return DjangoIntelligenceService(ctx).get_url_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_migration_schema(ctx: Context, table_name: str = None) -> dict[str, Any]:
            """
            Parse Django migration files and extract database schema information.
            Returns fields (with types, nullable, defaults), indexes, and foreign keys
            from CreateModel, AddField, AlterField, and AddIndex operations.

            Args:
                table_name: Optional table name (e.g., "auth_user") or model name (e.g., "User").
                            If omitted, returns schema for all tables.
            """
            return DjangoIntelligenceService(ctx).get_migration_schema(table_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_template_dependencies(template_path: str, ctx: Context) -> dict[str, Any]:
            """
            Get all dependencies of a Django template file.
            Returns: parent (extends), included templates, blocks, loaded tag libraries,
            URL references, static references, template variables, filters, and form references.

            Args:
                template_path: Relative path to the template (e.g., "myapp/templates/myapp/index.html")
            """
            return DjangoIntelligenceService(ctx).get_template_dependencies(template_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete Django request flow from URL to response.
            "What happens when a user hits this endpoint?" — answered in one call.

            Traces: URL -> Middleware -> View (CBV/FBV) -> Form/Serializer ->
            Model operations -> Template/JSON response

            Args:
                entry_point: View class name ("UserListView"), function name ("user_list"),
                            or URL name ("user-list")
                method: Optional HTTP method (get, post, etc.). If omitted with CBV,
                       returns info for all defined methods.
            """
            return DjangoIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_cache_map(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Map all cache usage in the Django project.
            Finds cache.get/set/delete, @cache_page, @vary_on_cookie, cached_property.
            Maps model signal receivers to cache invalidation.

            Args:
                model_name: Optional model name to filter signal-based invalidation.
                            If omitted, scans all models.
            """
            return DjangoIntelligenceService(ctx).get_cache_map(model_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_middleware_chain(ctx: Context, url_path: str = None) -> dict[str, Any]:
            """
            Show the full Django middleware stack from settings.py.
            Also finds custom middleware classes and per-view middleware decorators
            (@csrf_exempt, @login_required, @permission_required, etc.).

            Args:
                url_path: Optional URL path to find relevant per-view middleware.
                         If omitted, returns the full middleware stack.
            """
            return DjangoIntelligenceService(ctx).get_middleware_chain(url_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_form_validation(view_name: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the full form validation pipeline and flag mismatches.
            Maps: View -> Form/ModelForm -> fields -> validators -> clean methods ->
            template rendering. Detects field mismatches between form and model.

            Args:
                view_name: View class or function name (e.g., "UserCreateView")
            """
            return DjangoIntelligenceService(ctx).get_form_validation(view_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_django_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Django endpoint: URL pattern exists, view handles HTTP method,
            permission classes, authentication requirements, and middleware.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE)
                url: URL path (e.g., "/api/users/")
            """
            return DjangoIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Django project codebase.

            Args:
                pattern_type: One of: "naming" (app/model/view/url naming patterns),
                             "testing" (test patterns and frameworks),
                             "authentication" (auth backends, decorators, DRF auth),
                             "orm" (query patterns, N+1 risks, raw SQL),
                             "api" (DRF serializers, viewsets, routers, pagination)
            """
            return DjangoIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the Django project.
            Use before implementing new features to discover existing solutions.

            Searches for: form, model-admin, api-viewset, signal, celery-task,
            management-command, templatetag, middleware, mixin, decorator patterns.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "celery task", "model admin inline", "api viewset")
            """
            return DjangoIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire Django project.
            Use as the FIRST call in every new session to understand the full codebase.

            Returns: installed apps, models per app, URL count, middleware stack,
            templates, static files, management commands, celery tasks, signals,
            and project metrics.
            """
            return DjangoIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_django_serializer_chain(view_name: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the DRF serializer chain: ViewSet -> Serializer -> Model.
            Detects field mismatches between serializer and model, finds router
            registration, permission classes, and pagination.

            Args:
                view_name: ViewSet or APIView class name (e.g., "UserViewSet")
            """
            return DjangoIntelligenceService(ctx).get_serializer_chain(view_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def django_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a Django symbol.
            Combines reference tracking with context-aware deep checks based on file type.

            - model -> checks migrations, admin, serializers, forms
            - view -> checks urls, templates, serializers
            - form -> checks views, templates
            - serializer -> checks views, models
            - url -> checks views, template {% url %} tags

            Args:
                file_path: Relative file path (e.g., "myapp/models.py")
                symbol_name: Symbol to check (e.g., "User", "UserCreateView", "UserForm")
            """
            return DjangoIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["DjangoPlugin"]
