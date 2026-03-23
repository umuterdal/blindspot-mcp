"""React Native plugin for Blindspot MCP.

Provides React Native-specific intelligence tools:
- Component tree and dependency mapping
- React Navigation parsing (stack, tab, drawer, linking)
- Native module discovery (NativeModules, TurboModules, Expo)
- State management mapping (Redux, Zustand, MobX, Jotai, Context)
- Business flow tracing (screen -> navigation -> hook -> API -> store)
- Platform-specific code detection (.ios/.android, Platform.select)
- Style analysis (StyleSheet, NativeWind, styled-components)
- Screen verification
- Project convention extraction
- Similar pattern discovery
- Pre-edit impact analysis
"""

from typing import Any, Dict, List

from mcp.server.fastmcp import Context, FastMCP

from ..base_plugin import BlindspotPlugin


class ReactNativePlugin(BlindspotPlugin):
    """React Native framework plugin for Blindspot MCP."""

    @property
    def name(self) -> str:
        return "reactnative"

    @property
    def framework(self) -> str:
        return "reactnative"

    @property
    def description(self) -> str:
        return "React Native framework intelligence — components, navigation, native modules, stores, styles"

    def get_scan_dirs(self) -> Dict[str, str]:
        return {
            "screens": "src/screens",
            "components": "src/components",
            "navigation": "src/navigation",
            "stores": "src/stores",
            "services": "src/services",
            "hooks": "src/hooks",
            "utils": "src/utils",
            "native": "src/native",
            "tests": "__tests__",
        }

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "pattern": r"\bconsole\.log\s*\(",
                "severity": "warning",
                "message": "console.log() in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"\bconsole\.debug\s*\(",
                "severity": "warning",
                "message": "console.debug() in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"\bdebugger\b",
                "severity": "error",
                "message": "debugger statement in code — remove before commit",
                "file_types": ["ts", "tsx", "js", "jsx"],
            },
            {
                "pattern": r"style\s*=\s*\{\{",
                "severity": "info",
                "message": "Inline style object — use StyleSheet.create for performance",
                "file_types": ["tsx", "jsx"],
            },
            {
                "pattern": r"Dimensions\.get\s*\(\s*['\"](?:window|screen)['\"]\s*\)",
                "severity": "warning",
                "message": "Dimensions.get in render — use useWindowDimensions hook instead",
                "file_types": ["tsx", "jsx"],
            },
            {
                "pattern": r"=>\s*\{[^}]*\}\s*\)",
                "severity": "info",
                "message": "Anonymous function in render — extract or wrap in useCallback for performance",
                "file_types": ["tsx", "jsx"],
            },
            {
                "pattern": r"@ts-ignore",
                "severity": "warning",
                "message": "@ts-ignore suppresses type errors — use @ts-expect-error with explanation",
                "file_types": ["ts", "tsx"],
            },
        ]

    def register_tools(self, mcp: FastMCP) -> None:
        """Register all React Native-specific MCP tools."""
        from ...services.reactnative_intelligence_service import ReactNativeIntelligenceService
        from ...utils import handle_mcp_tool_errors

        # Import concurrency limiter from server module
        from ...server import with_concurrency_limit

        # ── 1. get_rn_component_tree ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_component_tree(ctx: Context, component_name: str = None) -> dict[str, Any]:
            """
            Parse .tsx/.jsx files for React Native components.
            Returns: props, hooks, StyleSheet styles, Platform.OS checks,
            native module imports, child components, and component hierarchy.

            Args:
                component_name: Optional specific component name (e.g., "ProfileScreen").
                                If omitted, returns all discovered components.
            """
            return ReactNativeIntelligenceService(ctx).get_component_tree(component_name)

        # ── 2. get_rn_navigation_map ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_navigation_map(ctx: Context, filter_prefix: str = None) -> dict[str, Any]:
            """
            Parse React Navigation: createStackNavigator, createBottomTabNavigator,
            createDrawerNavigator, NavigationContainer, linking config, deep links.
            Returns: screen -> component mapping, navigation params, param list types.

            Args:
                filter_prefix: Optional screen name prefix filter (e.g., "Auth").
            """
            return ReactNativeIntelligenceService(ctx).get_navigation_map(filter_prefix)

        # ── 3. get_rn_native_modules ───────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_native_modules(ctx: Context) -> dict[str, Any]:
            """
            Find native module usage: NativeModules, TurboModuleRegistry,
            requireNativeComponent, codegenNativeComponent.
            Maps JS -> native bridges. Detects Expo modules vs bare React Native.
            Scans iOS (.m/.mm/.swift) and Android (.java/.kt) bridge files.
            """
            return ReactNativeIntelligenceService(ctx).get_native_modules()

        # ── 4. get_rn_store_map ────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_store_map(ctx: Context, store_name: str = None) -> dict[str, Any]:
            """
            Parse Redux (createSlice/createAsyncThunk), Zustand (create),
            MobX (makeObservable), Jotai (atom), and React Context (createContext).
            Maps store -> screen consumer relationships.

            Args:
                store_name: Optional specific store/slice/atom name.
                            If omitted, returns all state management.
            """
            return ReactNativeIntelligenceService(ctx).get_store_map(store_name)

        # ── 5. get_rn_flow_map ─────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_flow_map(entry_point: str, ctx: Context, method: str = None) -> dict[str, Any]:
            """
            Trace a complete business flow:
            Screen -> navigation -> component -> hook -> API call -> store update -> re-render.

            Args:
                entry_point: Screen/component name (e.g., "ProfileScreen") or file path.
                method: Optional specific method/function to trace.
            """
            return ReactNativeIntelligenceService(ctx).get_flow_map(entry_point, method)

        # ── 6. get_rn_platform_specific ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_platform_specific(ctx: Context, file_path: str = None) -> dict[str, Any]:
            """
            Find platform-specific files (.ios.tsx/.android.tsx) and code
            (Platform.select, Platform.OS conditionals, Platform.Version checks).
            Detect platform-specific imports and conditional rendering.

            Args:
                file_path: Optional specific file to analyze.
                           If omitted, scans all project files.
            """
            return ReactNativeIntelligenceService(ctx).get_platform_specific(file_path)

        # ── 7. get_rn_style_map ────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_style_map(ctx: Context, component_path: str = None) -> dict[str, Any]:
            """
            Parse StyleSheet.create, styled-components, NativeWind/Tailwind CSS.
            Maps style -> component usage, detects unused styles, inline style usage.

            Args:
                component_path: Optional specific file path to analyze.
                                If omitted, scans all project files.
            """
            return ReactNativeIntelligenceService(ctx).get_style_map(component_path)

        # ── 8. verify_rn_screen ────────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def verify_rn_screen(screen_name: str, ctx: Context) -> dict[str, Any]:
            """
            Verify a React Native screen: component exists and is exported,
            navigation registration present, navigation params typed.

            Args:
                screen_name: Screen/component name to verify (e.g., "ProfileScreen").
            """
            return ReactNativeIntelligenceService(ctx).verify_screen(screen_name)

        # ── 9. get_rn_project_conventions ──────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_project_conventions(pattern_type: str, ctx: Context) -> dict[str, Any]:
            """
            Extract real coding conventions from the React Native project codebase.
            Analyzes actual files to determine the project's established patterns.

            Args:
                pattern_type: One of: "naming" (file/component naming),
                              "navigation" (stack/tab/drawer patterns, deep linking),
                              "state" (Redux/Zustand/MobX/Jotai/Context),
                              "styling" (StyleSheet/NativeWind/Tailwind/styled-components),
                              "testing" (Detox/Jest/RTL/Maestro),
                              "native" (TurboModules/Fabric/old bridge/Expo)
            """
            return ReactNativeIntelligenceService(ctx).get_project_conventions(pattern_type)

        # ── 10. get_rn_similar_patterns ────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_similar_patterns(description: str, ctx: Context) -> dict[str, Any]:
            """
            Find similar implementation patterns already in the React Native project.
            Use before implementing new features to discover existing solutions.
            Searches for: modal, bottom-sheet, list (FlatList/SectionList), form,
            auth-flow, onboarding, push-notification, deep-link, animation (Reanimated).

            Args:
                description: Natural language description of what you're looking for
                            (e.g., "bottom sheet", "FlatList with pagination", "push notification")
            """
            return ReactNativeIntelligenceService(ctx).get_similar_patterns(description)

        # ── 11. get_rn_project_snapshot ─────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_rn_project_snapshot(ctx: Context) -> dict[str, Any]:
            """
            Generate a compact snapshot of the entire React Native project structure.
            Returns: screens, navigation structure, stores, native modules,
            platform-specific files, Expo config, dependencies, scripts.
            ~5KB overview useful for project orientation.
            """
            return ReactNativeIntelligenceService(ctx).get_project_snapshot()

        # ── 12. pre_rn_edit_check ──────────────────────────────────

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def pre_rn_edit_check(file_path: str, symbol_name: str, ctx: Context) -> dict[str, Any]:
            """
            Meta-tool: check impact BEFORE editing a symbol in a React Native project.
            Context-aware analysis based on symbol type:
            - Screen: check navigation registration, navigation.navigate callers
            - Component: check JSX consumers, importers, prop usage
            - Hook: check all hook consumers
            - Store: check all store subscribers/selectors
            - Native module: check JS bridge callers, platform-specific implementations

            Args:
                file_path: Relative file path (e.g., "src/screens/ProfileScreen.tsx")
                symbol_name: Symbol to check (e.g., "ProfileScreen", "useAuth", "userStore")
            """
            return ReactNativeIntelligenceService(ctx).pre_edit_check(file_path, symbol_name)


__all__ = ["ReactNativePlugin"]
