"""Spring Boot plugin for Blindspot MCP.

Provides Spring Boot-specific intelligence tools:
- JPA entity relationship mapping
- Endpoint parsing (REST controllers)
- Schema extraction (JPA + Flyway/Liquibase)
- Thymeleaf template dependency analysis
- Spring Security filter chain resolution
- Cache annotation mapping
- Validation chain tracing
- Flow mapping (endpoint -> service -> repository)
- Dependency injection graph
- Project conventions detection
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class SpringPlugin(BlindspotPlugin):
    """Spring Boot framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "spring"

    @property
    def framework(self) -> str:
        return "spring"

    @property
    def description(self) -> str:
        return "Spring Boot framework intelligence — JPA, Security, REST, Thymeleaf, DI"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "java_src": "src/main/java",
            "kotlin_src": "src/main/kotlin",
            "resources": "src/main/resources",
            "templates": "src/main/resources/templates",
            "static": "src/main/resources/static",
            "test_java": "src/test/java",
            "test_kotlin": "src/test/kotlin",
            "migrations_flyway": "src/main/resources/db/migration",
            "migrations_liquibase": "src/main/resources/db/changelog",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"System\.out\.print(ln)?\s*\(",
                "severity": "error",
                "message": "System.out.println in code — use SLF4J logger instead",
                "file_types": ["java"],
            },
            {
                "pattern": r"System\.err\.print(ln)?\s*\(",
                "severity": "error",
                "message": "System.err.println in code — use SLF4J logger instead",
                "file_types": ["java"],
            },
            {
                "pattern": r"@Autowired\s*\n\s*(?:private|protected)",
                "severity": "warning",
                "message": "Field injection via @Autowired — prefer constructor injection for testability",
                "file_types": ["java"],
            },
            {
                "pattern": r"\.printStackTrace\s*\(",
                "severity": "error",
                "message": "printStackTrace() in code — use logger.error() with exception",
                "file_types": ["java", "kt"],
            },
            {
                "pattern": r'@SuppressWarnings\s*\(\s*"unchecked"\s*\)',
                "severity": "warning",
                "message": "@SuppressWarnings(\"unchecked\") — consider type-safe alternative",
                "file_types": ["java"],
            },
            {
                "pattern": r"Thread\.sleep\s*\(",
                "severity": "warning",
                "message": "Thread.sleep() in production code — use ScheduledExecutorService or @Async",
                "file_types": ["java", "kt"],
            },
            {
                "pattern": r"\bnew\s+Date\s*\(\s*\)",
                "severity": "warning",
                "message": "new Date() — use java.time API (Instant.now(), LocalDateTime.now())",
                "file_types": ["java"],
            },
            {
                "pattern": r"\$guarded\s*=\s*\[\s*\]",
                "severity": "warning",
                "message": "Empty catch block — at minimum log the exception",
                "file_types": ["java", "kt"],
            },
            {
                "pattern": r"catch\s*\(\s*\w+\s+\w+\s*\)\s*\{\s*\}",
                "severity": "warning",
                "message": "Empty catch block — at minimum log the exception",
                "file_types": ["java"],
            },
            {
                "pattern": r"@Transactional\s*\n.*(?:private|protected)\s+",
                "severity": "error",
                "message": "@Transactional on private/protected method has no effect — must be public",
                "file_types": ["java"],
            },
            {
                "pattern": r"return\s+null\s*;",
                "severity": "warning",
                "message": "Returning null — consider Optional<T> or throwing an exception",
                "file_types": ["java"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Spring Boot-specific MCP tools."""
        from ...services.spring_intelligence_service import SpringIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_entity_relationships(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Extract JPA entity relationship map from @Entity classes.
            Returns relationships (@OneToMany, @ManyToOne, @ManyToMany, @OneToOne),
            join columns, fetch types, cascade settings, and column definitions.

            Args:
                entity_name: Optional specific entity class name (e.g., "User").
                            If omitted, returns all entities.
            """
            return SpringIntelligenceService(ctx).get_entity_relationships(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_endpoint_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse @RestController/@Controller classes and return endpoint -> handler mapping.
            Extracts @GetMapping, @PostMapping, @PutMapping, @DeleteMapping, @PatchMapping,
            @RequestMapping with path, HTTP method, produces/consumes, and security annotations.

            Args:
                filter_prefix: Optional URL prefix filter (e.g., "/api/users")
            """
            return SpringIntelligenceService(ctx).get_endpoint_map(filter_prefix)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_schema_info(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Parse JPA entity fields and migration files to build database schema.
            Combines @Column, @Id, @GeneratedValue from entities with Flyway (V*.sql)
            and Liquibase changelog migrations.

            Args:
                entity_name: Optional entity class name (e.g., "User").
                            If omitted, returns schema for all entities.
            """
            return SpringIntelligenceService(ctx).get_schema_info(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_template_dependencies(template_path: str, ctx: Context) -> dict[str, Any]:
            """
            Get all dependencies of a Thymeleaf template file.
            Returns: fragments included/replaced, iterations, conditionals,
            variable expressions, link expressions, form fields, and which controller renders it.

            Args:
                template_path: Relative path to the template
                              (e.g., "src/main/resources/templates/users/list.html"
                               or just "users/list.html")
            """
            return SpringIntelligenceService(ctx).get_template_dependencies(template_path)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_spring_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete request flow from endpoint to data layer.
            "What happens when a user hits this endpoint?" — answered in one call.

            Traces: Endpoint -> Security (@PreAuthorize) -> Controller -> @Autowired Service ->
            Repository -> Entity. Detects @Transactional boundaries and event publishing.

            Args:
                entry_point: Controller name ("UserController") or URL path ("/api/users")
                method: Optional method name. If omitted with controller name,
                       returns flows for ALL handler methods.
            """
            return SpringIntelligenceService(ctx).get_flow_map(entry_point, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_cache_map(ctx: Context, entity_name: str = None) -> dict[str, Any]:
            """
            Map all Spring Cache annotations (@Cacheable, @CacheEvict, @CachePut, @Caching).
            Builds cache_name -> {readers, writers, evictors} mapping and detects
            potential stale cache issues.

            Args:
                entity_name: Optional entity/service name filter (e.g., "UserService").
                            If omitted, scans ALL classes.
            """
            return SpringIntelligenceService(ctx).get_cache_map(entity_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_filter_chain(ctx: Context, endpoint: str = None) -> dict[str, Any]:
            """
            Parse Spring Security configuration and return filter chain rules.
            Parses SecurityFilterChain, http.authorizeHttpRequests(), .requestMatchers(),
            .hasRole(), @PreAuthorize, @Secured. Returns per-endpoint security stack.

            Args:
                endpoint: Optional URL path to check security for (e.g., "/api/users").
                         If omitted, returns full security configuration.
            """
            return SpringIntelligenceService(ctx).get_filter_chain(endpoint)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_spring_validation_chain(controller: str, method: str, ctx: Context) -> dict[str, Any]:
            """
            Trace the full validation pipeline from @Valid DTO through constraint annotations.
            Cross-references @Valid parameter -> @NotNull/@Size/@Pattern constraints on DTO ->
            controller handler -> response. Detects missing validations.

            Args:
                controller: Controller class name (e.g., "UserController")
                method: Handler method name (e.g., "createUser")
            """
            return SpringIntelligenceService(ctx).get_validation_chain(controller, method)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_spring_endpoint(method: str, url: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Spring Boot endpoint: mapping exists, handler params match,
            security configured, validation present. Returns pass/warning/fail status.

            Args:
                method: HTTP method (GET, POST, PUT, DELETE, PATCH)
                url: URL path (e.g., "/api/users/{id}")
            """
            return SpringIntelligenceService(ctx).verify_endpoint(method, url)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_spring_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real conventions from the Spring Boot project codebase.
            Analyzes actual code to find patterns in naming, dependency injection,
            exception handling, testing, and logging.

            Args:
                pattern_type: One of: "naming", "injection", "exception", "testing", "logging"
            """
            return SpringIntelligenceService(ctx).get_project_conventions(pattern_type)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_spring_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the Spring Boot project.
            Use before implementing new features to discover existing solutions.
            Supports: pagination, file-upload, authentication, jwt, email, scheduler,
            event-listener, aspect, interceptor, websocket.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "pagination", "file upload", "jwt authentication")
            """
            return SpringIntelligenceService(ctx).get_similar_patterns(description)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_spring_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Get a high-level overview of the Spring Boot project.
            Returns: entities, controllers, services, repositories, configurations,
            security rules, scheduled tasks, profiles, application properties, and dependencies.
            """
            return SpringIntelligenceService(ctx).get_project_snapshot()

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_dependency_injection(ctx: Context, class_name: str = None) -> dict[str, Any]:
            """
            Map @Autowired/@Inject fields and constructor injection across the project.
            Builds a dependency graph showing who injects what, detects circular dependencies,
            and identifies field vs constructor injection patterns.

            Args:
                class_name: Optional class name to inspect (e.g., "UserService").
                           If omitted, builds full DI graph for the project.
            """
            return SpringIntelligenceService(ctx).get_dependency_injection(class_name)

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def spring_pre_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a Spring Boot project.
            Combines ripple-effect analysis with contextual deep checks based on file type:
            - Entity: checks repositories, services, DTOs, migrations
            - Controller: checks security config, validation, tests
            - Service: checks consuming controllers, other services, tests

            Args:
                file_path: Relative file path (e.g., "src/main/java/com/example/model/User.java")
                symbol_name: Symbol to check (e.g., "findByEmail", "createUser", "email")
            """
            return SpringIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["SpringPlugin"]
