"""Flutter/Dart plugin for Blindspot MCP.

Provides Flutter-specific intelligence tools:
- Widget tree and hierarchy mapping
- Route parsing (GoRouter, Navigator, auto_route)
- Model schema extraction (freezed, json_serializable, manual)
- State management mapping (Riverpod, BLoC, Provider)
- Business flow tracing (route -> widget -> state -> repository -> API)
- Dependency injection graph (get_it, injectable, riverpod)
- Asset and font mapping
- Route verification
- Project convention detection
- Similar pattern discovery
- Pre-edit impact analysis
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class FlutterPlugin(BlindspotPlugin):
    """Flutter/Dart framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "flutter"

    @property
    def framework(self) -> str:
        return "flutter"

    @property
    def description(self) -> str:
        return "Flutter/Dart framework intelligence — widgets, routes, state management, models, assets"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "screens": "lib/screens",
            "widgets": "lib/widgets",
            "models": "lib/models",
            "providers": "lib/providers",
            "blocs": "lib/blocs",
            "services": "lib/services",
            "repositories": "lib/repositories",
            "routes": "lib",
            "utils": "lib/utils",
            "tests": "test",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bprint\s*\(",
                "severity": "warning",
                "message": "print() in code — use debugPrint() or a logger package instead",
                "file_types": ["dart"],
            },
            {
                "pattern": r"setState\s*\(",
                "severity": "info",
                "message": "setState in StatelessWidget — verify this is in a StatefulWidget State class",
                "file_types": ["dart"],
            },
            {
                "pattern": r"BuildContext\s+\w+.*\basync\b",
                "severity": "warning",
                "message": "BuildContext used across async gap — context may be invalid after await",
                "file_types": ["dart"],
            },
            {
                "pattern": r"""(?:Text|title|label)\s*[:(\s]+\s*['"][A-Z]""",
                "severity": "info",
                "message": "Hardcoded string — consider using localization (l10n/intl)",
                "file_types": ["dart"],
            },
            {
                "pattern": r"\bdebugger\s*\(",
                "severity": "error",
                "message": "debugger() statement in code — remove before commit",
                "file_types": ["dart"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all Flutter-specific MCP tools."""
        from ...services.flutter_intelligence_service import FlutterIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        # ── 1. get_widget_tree ─────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_widget_tree(ctx: Context, widget_name: str = None) -> dict[str, Any]:
            """
            Parse Dart files for StatelessWidget/StatefulWidget/HookWidget/ConsumerWidget classes.
            Returns: widget type, build method, child widgets, State class, initState/dispose,
            props, and widget hierarchy from imports and build method analysis.

            Args:
                widget_name: Optional specific widget name (e.g., "HomeScreen").
                             If omitted, returns all discovered widgets.
            """
            return FlutterIntelligenceService(ctx).get_widget_tree(widget_name)

        # ── 2. get_flutter_route_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_route_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse GoRouter routes, Navigator routes, and auto_route definitions.
            Returns: path, builder/page widget, redirect guards, nested routes, route names.

            Args:
                filter_prefix: Optional route path prefix filter (e.g., "/auth").
            """
            return FlutterIntelligenceService(ctx).get_route_map(filter_prefix)

        # ── 3. get_dart_model_schema ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_dart_model_schema(ctx: Context, model_name: str = None) -> dict[str, Any]:
            """
            Parse Dart model classes: factory fromJson, toJson, copyWith,
            freezed/json_serializable annotations.
            Returns: fields, types, nullable, defaults, JSON key mappings, union cases.

            Args:
                model_name: Optional specific model name (e.g., "User").
                            If omitted, returns all discovered models.
            """
            return FlutterIntelligenceService(ctx).get_model_schema(model_name)

        # ── 4. get_flutter_provider_map ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_provider_map(ctx: Context, provider_name: str = None) -> dict[str, Any]:
            """
            Parse Riverpod (Provider/StateNotifierProvider/FutureProvider/StreamProvider/NotifierProvider),
            BLoC (Bloc/Cubit/BlocProvider), and Provider package state management.
            Maps provider/bloc to consumer widgets (ref.watch, ref.read, context.watch, BlocBuilder).

            Args:
                provider_name: Optional specific provider or bloc name.
                               If omitted, returns all state management.
            """
            return FlutterIntelligenceService(ctx).get_provider_map(provider_name)

        # ── 5. get_flutter_flow_map ────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete business flow:
            Route -> middleware/guard -> screen widget -> BLoC/provider -> repository -> API service -> model.

            Args:
                entry_point: Widget name (e.g., "HomeScreen") or route path (e.g., "/home").
                method: Optional method to trace specifically.
            """
            return FlutterIntelligenceService(ctx).get_flow_map(entry_point, method)

        # ── 6. get_flutter_dependency_injection ────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_dependency_injection(ctx: Context) -> dict[str, Any]:
            """
            Parse dependency injection setup: get_it (GetIt.instance.registerSingleton),
            injectable annotations, riverpod providers.
            Builds dependency graph from constructor injections.
            """
            return FlutterIntelligenceService(ctx).get_dependency_injection()

        # ── 7. get_flutter_asset_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_asset_map(ctx: Context) -> dict[str, Any]:
            """
            Parse pubspec.yaml assets and fonts. Find unused assets and missing asset references.
            Returns: declared assets, fonts, used assets, unused assets, missing references.
            """
            return FlutterIntelligenceService(ctx).get_asset_map()

        # ── 8. verify_flutter_route ────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_flutter_route(route_path: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a Flutter route: definition exists, target widget exists, guards configured.

            Args:
                route_path: The route path to verify (e.g., "/home", "/auth/login").
            """
            return FlutterIntelligenceService(ctx).verify_route(route_path)

        # ── 9. get_flutter_project_conventions ─────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real coding conventions from the Flutter project codebase.
            Analyzes actual files to determine the project's established patterns.

            Args:
                pattern_type: One of: "naming" (file/class naming),
                              "state" (Riverpod/BLoC/Provider/GetX),
                              "architecture" (clean arch/MVVM/MVC/feature-first),
                              "testing" (widget test/unit test/integration test),
                              "localization" (ARB/intl/easy_localization)
            """
            return FlutterIntelligenceService(ctx).get_project_conventions(pattern_type)

        # ── 10. get_flutter_similar_patterns ───────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the Flutter project.
            Use before implementing new features to discover existing solutions.
            Searches for: form, list-view, bottom-sheet, dialog, drawer, snackbar,
            animation, custom-painter, platform-channel patterns.

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "bottom sheet", "form validation", "animation controller")
            """
            return FlutterIntelligenceService(ctx).get_similar_patterns(description)

        # ── 11. get_flutter_project_snapshot ────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_flutter_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire Flutter project structure.
            Returns: screens, widgets by type, providers/blocs, routes, assets,
            pubspec dependencies, platform folders (android/ios/web/macos/linux/windows).
            ~5KB overview useful for project orientation.
            """
            return FlutterIntelligenceService(ctx).get_project_snapshot()

        # ── 12. pre_flutter_edit_check ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_flutter_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a Flutter project.
            Context-aware analysis based on symbol type:
            - Widget: check parent/child tree, route registration
            - Provider: check all consumers (ref.watch/ref.read)
            - BLoC/Cubit: check BlocBuilder/BlocListener consumers
            - Model: check serialization usage, repository references

            Args:
                file_path: Relative file path (e.g., "lib/screens/home_screen.dart")
                symbol_name: Symbol to check (e.g., "HomeScreen", "userProvider", "UserModel")
            """
            return FlutterIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["FlutterPlugin"]
