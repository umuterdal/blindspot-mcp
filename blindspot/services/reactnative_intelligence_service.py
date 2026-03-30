"""
React Native Intelligence Service - React Native-specific code analysis.

Provides cross-file component tree analysis, React Navigation parsing,
native module mapping, store discovery (Redux/Zustand/MobX/Jotai),
platform-specific code detection, style analysis, and convention detection
for React Native projects.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class ReactNativeIntelligenceService(BaseService):
    """React Native-specific code intelligence for RN projects."""

    SCAN_DIRS = {
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

    # ── Pattern registries ─────────────────────────────────────────────
    SIMILAR_PATTERN_REGISTRY = {
        "modal": {
            "keywords": ["modal", "popup", "overlay", "dialog"],
            "file_patterns": [
                r"<Modal\b", r"Modal\.(?:show|open)", r"useModal",
                r"react-native-modal", r"BottomSheetModal",
            ],
            "description": "Modal / dialog pattern",
        },
        "bottom-sheet": {
            "keywords": ["bottom", "sheet", "bottomsheet", "panel"],
            "file_patterns": [
                r"BottomSheet\b", r"@gorhom/bottom-sheet",
                r"BottomSheetModal", r"BottomSheetView",
            ],
            "description": "Bottom sheet / sliding panel pattern",
        },
        "list": {
            "keywords": ["list", "flatlist", "sectionlist", "scroll", "infinite", "pagination"],
            "file_patterns": [
                r"<FlatList\b", r"<SectionList\b", r"<FlashList\b",
                r"onEndReached", r"ListRenderItem", r"keyExtractor",
            ],
            "description": "List / FlatList / SectionList pattern",
        },
        "form": {
            "keywords": ["form", "input", "textinput", "validation", "submit"],
            "file_patterns": [
                r"<TextInput\b", r"useForm", r"react-hook-form",
                r"Formik", r"onSubmit", r"handleSubmit",
            ],
            "description": "Form with input and validation pattern",
        },
        "auth-flow": {
            "keywords": ["auth", "login", "signup", "register", "token", "session"],
            "file_patterns": [
                r"(?:login|signIn|authenticate)\s*\(", r"useAuth",
                r"AuthContext", r"authToken", r"refreshToken",
            ],
            "description": "Authentication / login flow pattern",
        },
        "onboarding": {
            "keywords": ["onboarding", "tutorial", "walkthrough", "intro", "welcome"],
            "file_patterns": [
                r"Onboarding\b", r"onboardingStep", r"AppIntro",
                r"react-native-onboarding", r"Swiper\b",
            ],
            "description": "Onboarding / tutorial flow pattern",
        },
        "push-notification": {
            "keywords": ["push", "notification", "fcm", "messaging", "apns"],
            "file_patterns": [
                r"messaging\(\)", r"getToken\(\)", r"onMessage",
                r"PushNotification", r"notifee", r"@react-native-firebase/messaging",
            ],
            "description": "Push notification pattern",
        },
        "deep-link": {
            "keywords": ["deep", "link", "deeplink", "universal", "linking"],
            "file_patterns": [
                r"Linking\.(?:getInitialURL|addEventListener)", r"linking:\s*\{",
                r"useDeepLink", r"deepLink", r"getInitialURL",
            ],
            "description": "Deep linking / universal links pattern",
        },
        "animation": {
            "keywords": ["animation", "animate", "reanimated", "animated", "gesture", "transition"],
            "file_patterns": [
                r"useSharedValue", r"useAnimatedStyle", r"withTiming",
                r"withSpring", r"Animated\.(?:View|Text|Image)",
                r"createAnimatedComponent", r"Gesture(?:Detector|Handler)",
            ],
            "description": "Animation / Reanimated / Gesture pattern",
        },
    }

    CONVENTION_PATTERNS = {
        "naming": {
            "description": "File and component naming conventions",
            "checks": ["file_naming", "component_naming", "hook_naming", "screen_naming"],
        },
        "navigation": {
            "description": "Navigation structure conventions",
            "checks": ["stack_nav", "tab_nav", "drawer_nav", "deep_linking", "params_typing"],
        },
        "state": {
            "description": "State management conventions",
            "checks": ["redux", "zustand", "mobx", "jotai", "context", "react_query"],
        },
        "styling": {
            "description": "Styling conventions",
            "checks": ["stylesheet", "nativewind", "tailwind", "styled_components", "inline"],
        },
        "testing": {
            "description": "Testing conventions",
            "checks": ["jest", "detox", "rtl", "maestro"],
        },
        "native": {
            "description": "Native bridge conventions",
            "checks": ["turbo_modules", "fabric", "old_arch", "expo_modules"],
        },
    }

    # ── Internal helpers ───────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the React Native project root path.

        In a monorepo, finds the workspace containing React Native.
        """
        try:
            base = self.base_path
            if not base or not os.path.isdir(base):
                return None

            # Check if base itself is a RN project
            pkg = os.path.join(base, "package.json")
            if os.path.isfile(pkg):
                try:
                    import json
                    with open(pkg, "r") as f:
                        data = json.load(f)
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    if "react-native" in deps:
                        return base
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

            # Monorepo: search common workspace directories
            for name in ["mobile", "app", "client", "native", "packages"]:
                sub = os.path.join(base, name)
                sub_pkg = os.path.join(sub, "package.json")
                if os.path.isfile(sub_pkg):
                    try:
                        import json
                        with open(sub_pkg, "r") as f:
                            data = json.load(f)
                        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                        if "react-native" in deps:
                            return sub
                    except Exception:
                        continue

            return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    def _collect_rn_files(
        self, base: str, subdirs: Optional[List[str]] = None
    ) -> List[Tuple[str, str]]:
        """Collect all .tsx/.jsx/.ts/.js files under given subdirectories.

        Returns list of (absolute_path, relative_path) tuples.
        """
        results: List[Tuple[str, str]] = []
        dirs_to_scan = subdirs or ["src", "app", "__tests__"]
        extensions = (".tsx", ".jsx", ".ts", ".js")

        for subdir in dirs_to_scan:
            full = os.path.join(base, subdir)
            if not os.path.isdir(full):
                continue
            for root, dirs_list, files in os.walk(full):
                # Skip node_modules, build directories
                dirs_list[:] = [
                    d for d in dirs_list
                    if d not in ("node_modules", ".expo", "build", "dist", "android", "ios")
                ]
                for fname in files:
                    if fname.endswith(extensions) and not fname.endswith(".d.ts"):
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, base)
                        results.append((fpath, rel))
        return results

    def _resolve_rn_file_path(self, base: str, file_path: str) -> Optional[str]:
        """
        Resolve RN file paths for both workspace-relative and repo-relative inputs.

        Examples:
        - app/screens/OnboardingScreen.tsx
        - mobile/app/screens/OnboardingScreen.tsx
        """
        normalized = (file_path or "").strip().lstrip("./")
        if not normalized:
            return None

        candidates = [
            os.path.join(base, normalized),
            os.path.join(os.path.dirname(base), normalized),
        ]

        if os.path.basename(base).lower() == "mobile" and normalized.startswith("mobile/"):
            candidates.append(os.path.join(base, normalized.split("/", 1)[1]))

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    def _read_file(self, fpath: str) -> Optional[str]:
        """Read file content safely."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def _parse_imports(self, content: str) -> List[Dict[str, Any]]:
        """Parse JS/TS import statements."""
        imports: List[Dict[str, Any]] = []
        # import X from 'Y'
        # import { A, B } from 'Y'
        # import * as Z from 'Y'
        # const X = require('Y')
        import_pat = re.compile(
            r"""import\s+(?:"""
            r"""(?:(\w+)(?:\s*,\s*)?)?"""  # default import
            r"""(?:\{\s*([\w\s,]+)\s*\})?"""  # named imports
            r"""(?:\*\s+as\s+(\w+))?"""  # namespace import
            r""")\s+from\s+['"]([^'"]+)['"]""",
        )
        for m in import_pat.finditer(content):
            imp: Dict[str, Any] = {"source": m.group(4)}
            if m.group(1):
                imp["default"] = m.group(1)
            if m.group(2):
                imp["named"] = [n.strip() for n in m.group(2).split(",") if n.strip()]
            if m.group(3):
                imp["namespace"] = m.group(3)
            imports.append(imp)

        # require
        require_pat = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
        for m in require_pat.finditer(content):
            imports.append({"default": m.group(1), "source": m.group(2), "type": "require"})

        return imports

    def _extract_components(self, content: str, rel_path: str) -> List[Dict[str, Any]]:
        """Extract React component declarations from file content."""
        components: List[Dict[str, Any]] = []

        # Function components: export [default] function ComponentName(props)
        func_pat = re.compile(
            r"(?:export\s+(?:default\s+)?)?function\s+([A-Z]\w+)\s*\(\s*"
            r"(?:\{\s*([\w\s,:.=?<>|&]+)\s*\})?(?:\s*:\s*([\w<>|&\s]+))?\s*\)",
        )
        for m in func_pat.finditer(content):
            comp_name = m.group(1)
            props_destructured = m.group(2)
            props_type = m.group(3)

            comp: Dict[str, Any] = {
                "name": comp_name,
                "type": "function",
                "file": rel_path,
                "is_default_export": "export default" in content[:m.start()].split("\n")[-1]
                                     if m.start() > 0 else False,
            }

            # Parse destructured props
            if props_destructured:
                props = self._parse_props_from_destructuring(props_destructured)
                comp["props"] = props
            if props_type:
                comp["props_type"] = props_type.strip()

            components.append(comp)

        # Arrow function components: export const ComponentName = (props) => { ... }
        # or: const ComponentName: React.FC<Props> = (props) => { ... }
        arrow_pat = re.compile(
            r"(?:export\s+(?:default\s+)?)?(?:const|let)\s+([A-Z]\w+)"
            r"(?:\s*:\s*React\.(?:FC|FunctionComponent)(?:<(\w+)>)?)?\s*=\s*"
            r"(?:\(\s*(?:\{\s*([\w\s,:.=?<>|&]+)\s*\})?(?:\s*:\s*([\w<>|&\s]+))?\s*\)"
            r"|(\w+))\s*(?::\s*[\w<>|&\s]+\s*)?=>"
        )
        for m in arrow_pat.finditer(content):
            comp_name = m.group(1)
            fc_type = m.group(2)
            props_destructured = m.group(3)
            props_type = m.group(4)
            single_param = m.group(5)

            # Skip non-component arrow functions (heuristic: must be PascalCase)
            if not comp_name[0].isupper():
                continue

            comp: Dict[str, Any] = {
                "name": comp_name,
                "type": "arrow",
                "file": rel_path,
            }
            if fc_type:
                comp["props_type"] = fc_type
            if props_destructured:
                comp["props"] = self._parse_props_from_destructuring(props_destructured)
            if props_type:
                comp["props_type"] = props_type.strip()
            if single_param:
                comp["single_param"] = single_param

            components.append(comp)

        # memo wrapped: export const ComponentName = React.memo((...) => { ... })
        memo_pat = re.compile(
            r"(?:export\s+(?:default\s+)?)?(?:const|let)\s+([A-Z]\w+)\s*=\s*"
            r"(?:React\.)?memo\s*\(",
        )
        for m in memo_pat.finditer(content):
            comp_name = m.group(1)
            # Check if already found
            if not any(c["name"] == comp_name for c in components):
                components.append({
                    "name": comp_name,
                    "type": "memo",
                    "file": rel_path,
                })

        # forwardRef: export const ComponentName = React.forwardRef((...) => { ... })
        forward_ref_pat = re.compile(
            r"(?:export\s+(?:default\s+)?)?(?:const|let)\s+([A-Z]\w+)\s*=\s*"
            r"(?:React\.)?forwardRef\s*(?:<[^>]+>)?\s*\(",
        )
        for m in forward_ref_pat.finditer(content):
            comp_name = m.group(1)
            if not any(c["name"] == comp_name for c in components):
                components.append({
                    "name": comp_name,
                    "type": "forwardRef",
                    "file": rel_path,
                })

        # Enrich with hooks, styles, platform checks
        for comp in components:
            comp["hooks"] = self._extract_hooks(content)
            comp["has_styles"] = bool(re.search(r"StyleSheet\.create\s*\(", content))
            comp["has_platform_check"] = bool(
                re.search(r"Platform\.(OS|select|Version)", content)
            )
            comp["native_imports"] = self._find_native_imports(content)

        return components

    def _parse_props_from_destructuring(self, destructured: str) -> List[Dict[str, Any]]:
        """Parse prop names from destructuring pattern."""
        props: List[Dict[str, Any]] = []
        # Split by comma, handling nested types
        depth = 0
        current = ""
        for ch in destructured:
            if ch in "<({":
                depth += 1
            elif ch in ">)}":
                depth -= 1
            if ch == "," and depth == 0:
                prop = self._parse_single_prop(current.strip())
                if prop:
                    props.append(prop)
                current = ""
            else:
                current += ch
        if current.strip():
            prop = self._parse_single_prop(current.strip())
            if prop:
                props.append(prop)
        return props

    def _parse_single_prop(self, prop_str: str) -> Optional[Dict[str, Any]]:
        """Parse a single prop from destructuring."""
        if not prop_str:
            return None

        # Handle: name: alias, name = default, name?: type
        m = re.match(
            r"(\w+)(?:\s*:\s*(\w+))?(?:\s*\?\s*)?(?:\s*:\s*([\w<>|&\[\]]+))?"
            r"(?:\s*=\s*(.+))?",
            prop_str,
        )
        if not m:
            return None

        prop: Dict[str, Any] = {"name": m.group(1)}
        if m.group(2) and m.group(2) != m.group(1):
            prop["alias"] = m.group(2)
        if m.group(3):
            prop["type"] = m.group(3)
        if m.group(4):
            prop["default"] = m.group(4).strip()
        if "?" in prop_str.split(m.group(1))[-1].split(",")[0][:3]:
            prop["optional"] = True

        return prop

    def _extract_hooks(self, content: str) -> List[str]:
        """Extract React hook usage from content."""
        hooks: List[str] = []
        hook_pat = re.compile(r"\b(use[A-Z]\w+)\s*\(")
        seen: Set[str] = set()
        for m in hook_pat.finditer(content):
            hook_name = m.group(1)
            if hook_name not in seen:
                hooks.append(hook_name)
                seen.add(hook_name)
        return hooks[:30]

    def _find_native_imports(self, content: str) -> List[str]:
        """Find imports of native modules."""
        native_imports: List[str] = []
        patterns = [
            r"(?:NativeModules|TurboModuleRegistry)\.(\w+)",
            r"requireNativeComponent\s*\(\s*['\"](\w+)['\"]",
            r"from\s+['\"]react-native['\"].*\b(NativeModules|TurboModuleRegistry)\b",
        ]
        for pat in patterns:
            for m in re.finditer(pat, content):
                native_imports.append(m.group(1))
        return native_imports

    def _extract_stylesheet(self, content: str) -> Dict[str, List[str]]:
        """Extract StyleSheet.create definitions."""
        styles: Dict[str, List[str]] = {}
        # StyleSheet.create({ container: {...}, title: {...} })
        ss_pat = re.compile(r"StyleSheet\.create\s*\(\s*\{", re.DOTALL)
        for m in ss_pat.finditer(content):
            # Extract style keys
            brace_start = m.end() - 1
            depth = 0
            i = brace_start
            while i < len(content):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block = content[brace_start + 1 : i]
                        # Find top-level keys
                        key_pat = re.compile(r"^\s*(\w+)\s*:", re.MULTILINE)
                        keys = [km.group(1) for km in key_pat.finditer(block)]
                        var_match = re.search(
                            r"(?:const|let|var)\s+(\w+)\s*=\s*StyleSheet\.create",
                            content[:m.start()],
                        )
                        var_name = var_match.group(1) if var_match else "styles"
                        styles[var_name] = keys
                        break
                i += 1
        return styles

    # ── 1. get_component_tree ──────────────────────────────────────────

    def get_component_tree(self, component_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse .tsx/.jsx files for React Native components.

        Extracts: props, hooks, StyleSheet styles, Platform.OS checks, native module imports.

        Args:
            component_name: Optional specific component name. If omitted, returns all.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {"components": {}, "hierarchy": {}, "total": 0}
        rn_files = self._collect_rn_files(base, ["src", "app", "screens", "components"])

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            components = self._extract_components(content, rel_path)
            for comp in components:
                name = comp["name"]
                if component_name and name != component_name:
                    continue

                # Extract child component usage from JSX
                jsx_children = self._find_jsx_children(content, name)
                comp["children"] = jsx_children

                # Extract StyleSheet details
                comp["style_sheets"] = self._extract_stylesheet(content)

                results["components"][name] = comp

        # Build hierarchy
        for cname, cinfo in results["components"].items():
            children_in_project = [
                c for c in cinfo.get("children", []) if c in results["components"]
            ]
            if children_in_project:
                results["hierarchy"][cname] = children_in_project

        results["total"] = len(results["components"])
        return results

    def _find_jsx_children(self, content: str, component_name: str) -> List[str]:
        """Find child component references in JSX."""
        children: List[str] = []
        seen: Set[str] = set()
        # Find PascalCase JSX elements: <ComponentName
        jsx_pat = re.compile(r"<([A-Z]\w+)(?:\s|/|>)")
        for m in jsx_pat.finditer(content):
            child = m.group(1)
            if child != component_name and child not in seen:
                # Exclude common RN primitives
                if child not in (
                    "View", "Text", "Image", "ScrollView", "TouchableOpacity",
                    "TouchableHighlight", "TouchableWithoutFeedback", "Pressable",
                    "TextInput", "FlatList", "SectionList", "SafeAreaView",
                    "KeyboardAvoidingView", "StatusBar", "ActivityIndicator",
                    "Modal", "Alert", "Switch", "Button", "StyleSheet",
                    "Platform", "Dimensions", "Animated", "Easing",
                    "Fragment", "React", "Provider", "NavigationContainer",
                    "Stack", "Tab", "Drawer",
                ):
                    children.append(child)
                    seen.add(child)
        return children[:30]

    # ── 2. get_navigation_map ──────────────────────────────────────────

    def get_navigation_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse React Navigation: createStackNavigator, createBottomTabNavigator,
        createDrawerNavigator, NavigationContainer.

        Extracts: screen -> component mapping, params, linking config, deep links.

        Args:
            filter_prefix: Optional screen name prefix filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "navigators": [],
            "screens": [],
            "linking_config": None,
            "deep_links": [],
            "params": {},
            "total_screens": 0,
        }

        rn_files = self._collect_rn_files(base, ["src", "app", "navigation"])

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── Navigator creation ──────────────────────────────────
            # createStackNavigator, createNativeStackNavigator, createBottomTabNavigator,
            # createDrawerNavigator, createMaterialTopTabNavigator
            nav_create_pat = re.compile(
                r"(?:const|let)\s+(\w+)\s*=\s*create(\w+Navigator)\s*(?:<(\w+)>)?\s*\("
            )
            for nm in nav_create_pat.finditer(content):
                nav_var = nm.group(1)
                nav_type = nm.group(2)
                param_type = nm.group(3)

                navigator: Dict[str, Any] = {
                    "variable": nav_var,
                    "type": nav_type,
                    "file": rel_path,
                    "screens": [],
                }
                if param_type:
                    navigator["param_list_type"] = param_type

                # Find <Nav.Screen> registrations
                screen_pat = re.compile(
                    rf"""<{re.escape(nav_var)}\.Screen\s+"""
                    r"""name\s*=\s*(?:\{{?\s*)?['"]([^'"]+)['"](?:\s*\}}?)?"""
                    r"""(?:\s+component\s*=\s*\{{?\s*(\w+)\s*\}}?)?"""
                    r"""(?:[^>]*options\s*=\s*\{{([^}}]*)\}})?"""
                )
                for sm in screen_pat.finditer(content):
                    screen_name = sm.group(1)
                    component = sm.group(2)
                    options_str = sm.group(3)

                    if filter_prefix and not screen_name.startswith(filter_prefix):
                        continue

                    screen_info: Dict[str, Any] = {
                        "name": screen_name,
                        "file": rel_path,
                    }
                    if component:
                        screen_info["component"] = component
                    if options_str:
                        # Extract title
                        title_match = re.search(r"""title:\s*['"]([^'"]+)['"]""", options_str)
                        if title_match:
                            screen_info["title"] = title_match.group(1)
                        # Check for headerShown
                        if "headerShown: false" in options_str:
                            screen_info["headerShown"] = False

                    navigator["screens"].append(screen_info)
                    results["screens"].append(screen_info)

                # Also find inline component screens: <Nav.Screen name="X">{() => <Component />}</Nav.Screen>
                inline_screen_pat = re.compile(
                    rf"""<{re.escape(nav_var)}\.Screen\s+name\s*=\s*['"]([^'"]+)['"]"""
                    r"""[^>]*>\s*\{{?\s*(?:\([^)]*\)\s*=>)?\s*<(\w+)"""
                )
                for ism in inline_screen_pat.finditer(content):
                    screen_name = ism.group(1)
                    component = ism.group(2)
                    if filter_prefix and not screen_name.startswith(filter_prefix):
                        continue
                    screen_info = {
                        "name": screen_name,
                        "component": component,
                        "file": rel_path,
                        "inline": True,
                    }
                    if not any(s["name"] == screen_name for s in navigator["screens"]):
                        navigator["screens"].append(screen_info)
                        results["screens"].append(screen_info)

                results["navigators"].append(navigator)

            # ── Linking configuration ───────────────────────────────
            linking_pat = re.compile(
                r"(?:const|let)\s+\w*(?:linking|linkingConfig)\w*\s*(?::\s*\w+\s*)?=\s*\{",
                re.IGNORECASE,
            )
            if linking_pat.search(content):
                # Extract prefixes
                prefix_match = re.search(
                    r"""prefixes:\s*\[\s*((?:['"][^'"]+['"]\s*,?\s*)+)\]""", content
                )
                prefixes: List[str] = []
                if prefix_match:
                    prefixes = re.findall(r"""['"]([^'"]+)['"]""", prefix_match.group(1))

                # Extract screen paths from linking config
                screen_paths: Dict[str, str] = {}
                path_mappings = re.findall(
                    r"""(\w+)\s*:\s*(?:\{[^}]*path:\s*)?['"]([^'"]+)['"]""",
                    content,
                )
                for screen, path in path_mappings:
                    screen_paths[screen] = path

                results["linking_config"] = {
                    "file": rel_path,
                    "prefixes": prefixes,
                    "screen_paths": screen_paths,
                }

            # ── navigation.navigate / navigation.push calls ─────────
            nav_call_pat = re.compile(
                r"""navigation\.(navigate|push|replace|reset|goBack)\s*\(\s*['"](\w+)['"]"""
                r"""(?:\s*,\s*(\{[^}]*\}))?"""
            )
            for nc in nav_call_pat.finditer(content):
                action = nc.group(1)
                target_screen = nc.group(2)
                params_str = nc.group(3)
                if filter_prefix and not target_screen.startswith(filter_prefix):
                    continue
                if target_screen not in results["params"]:
                    results["params"][target_screen] = []
                if params_str:
                    param_keys = re.findall(r"(\w+)\s*:", params_str)
                    results["params"][target_screen].append({
                        "file": rel_path,
                        "action": action,
                        "params": param_keys,
                    })

            # ── Type-safe navigation params ─────────────────────────
            # type RootStackParamList = { Home: undefined; Profile: { userId: string } }
            param_list_pat = re.compile(
                r"type\s+(\w+ParamList)\s*=\s*\{([^}]+)\}",
                re.DOTALL,
            )
            for pl in param_list_pat.finditer(content):
                type_name = pl.group(1)
                body = pl.group(2)
                screen_params: Dict[str, Any] = {}
                for sp in re.finditer(r"(\w+)\s*:\s*(\{[^}]+\}|undefined|\w+)", body):
                    screen = sp.group(1)
                    param_type = sp.group(2).strip()
                    screen_params[screen] = param_type
                results["params"][f"_type:{type_name}"] = screen_params

        results["total_screens"] = len(results["screens"])
        return results

    # ── 3. get_native_modules ──────────────────────────────────────────

    def get_native_modules(self) -> Dict[str, Any]:
        """
        Find native module usage: NativeModules, TurboModuleRegistry,
        requireNativeComponent. Map JS -> native bridges.
        Detect Expo modules vs bare RN.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "is_expo": False,
            "expo_modules": [],
            "native_modules": [],
            "native_components": [],
            "turbo_modules": [],
            "bridge_files": [],
            "total": 0,
        }

        # Check if this is an Expo project
        app_json = os.path.join(base, "app.json")
        app_config = self._read_file(app_json)
        if app_config:
            try:
                config = json.loads(app_config)
                if "expo" in config:
                    results["is_expo"] = True
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Also check for expo in package.json
        pkg_json_path = os.path.join(base, "package.json")
        pkg_content = self._read_file(pkg_json_path)
        if pkg_content:
            try:
                pkg = json.loads(pkg_content)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "expo" in deps:
                    results["is_expo"] = True
                # Find expo-* packages
                for dep_name in deps:
                    if dep_name.startswith("expo-"):
                        results["expo_modules"].append(dep_name)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        rn_files = self._collect_rn_files(base, ["src", "app", "native", "modules"])

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # NativeModules.ModuleName
            nm_pat = re.compile(r"NativeModules\.(\w+)")
            for nm in nm_pat.finditer(content):
                module_name = nm.group(1)
                if not any(m["name"] == module_name for m in results["native_modules"]):
                    results["native_modules"].append({
                        "name": module_name,
                        "file": rel_path,
                        "type": "NativeModules",
                    })

            # TurboModuleRegistry.get/getEnforcing
            turbo_pat = re.compile(
                r"""TurboModuleRegistry\.(?:get|getEnforcing)\s*(?:<(\w+)>)?\s*\(\s*['"](\w+)['"]"""
            )
            for tm in turbo_pat.finditer(content):
                spec_type = tm.group(1)
                module_name = tm.group(2)
                results["turbo_modules"].append({
                    "name": module_name,
                    "spec": spec_type,
                    "file": rel_path,
                    "type": "TurboModule",
                })

            # requireNativeComponent
            rnc_pat = re.compile(
                r"""requireNativeComponent\s*(?:<(\w+)>)?\s*\(\s*['"](\w+)['"]"""
            )
            for rc in rnc_pat.finditer(content):
                type_param = rc.group(1)
                comp_name = rc.group(2)
                results["native_components"].append({
                    "name": comp_name,
                    "type_param": type_param,
                    "file": rel_path,
                })

            # codegenNativeComponent
            codegen_pat = re.compile(
                r"""codegenNativeComponent\s*(?:<(\w+)>)?\s*\(\s*['"](\w+)['"]"""
            )
            for cg in codegen_pat.finditer(content):
                comp_name = cg.group(2)
                results["native_components"].append({
                    "name": comp_name,
                    "file": rel_path,
                    "type": "codegen",
                })

        # Scan native (iOS/Android) bridge files
        for platform_dir in ["ios", "android"]:
            platform_path = os.path.join(base, platform_dir)
            if not os.path.isdir(platform_path):
                continue
            for root, _, files in os.walk(platform_path):
                for fname in files:
                    # iOS: .m, .mm, .swift files with RCT_EXPORT_MODULE
                    # Android: .java, .kt files with @ReactModule
                    if fname.endswith((".m", ".mm", ".swift", ".java", ".kt")):
                        fpath = os.path.join(root, fname)
                        content = self._read_file(fpath)
                        if not content:
                            continue

                        is_bridge = (
                            "RCT_EXPORT_MODULE" in content or
                            "RCT_EXPORT_METHOD" in content or
                            "@ReactModule" in content or
                            "ReactContextBaseJavaModule" in content or
                            "@objc" in content and "RCTBridgeModule" in content
                        )
                        if is_bridge:
                            rel = os.path.relpath(fpath, base)
                            # Extract module name
                            mod_name_match = (
                                re.search(r"RCT_EXPORT_MODULE\s*\(\s*(\w*)", content) or
                                re.search(r"""@ReactModule\s*\(\s*name\s*=\s*['"](\w+)""", content) or
                                re.search(r"getName\s*\(\)\s*\{[^}]*return\s*['\"](\w+)", content)
                            )
                            mod_name = mod_name_match.group(1) if mod_name_match else fname.split(".")[0]
                            results["bridge_files"].append({
                                "file": rel,
                                "module_name": mod_name or fname.split(".")[0],
                                "platform": platform_dir,
                            })

        results["total"] = (
            len(results["native_modules"]) +
            len(results["turbo_modules"]) +
            len(results["native_components"])
        )
        return results

    # ── 4. get_store_map ───────────────────────────────────────────────

    def get_store_map(self, store_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Redux/Zustand/MobX/Jotai stores and map to screen consumers.

        Args:
            store_name: Optional specific store name.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "state_management": "unknown",
            "stores": {},
            "atoms": {},
            "slices": {},
            "consumers": {},
            "total": 0,
        }

        rn_files = self._collect_rn_files(base, ["src", "app", "stores", "state", "redux"])

        # ── First pass: find store definitions ──────────────────────
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── Zustand stores ──────────────────────────────────────
            # export const useStore = create<StoreType>((set, get) => ({ ... }))
            zustand_pat = re.compile(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*create(?:<(\w+)>)?\s*\(",
            )
            for zm in zustand_pat.finditer(content):
                name = zm.group(1)
                type_param = zm.group(2)
                if store_name and name != store_name:
                    continue
                # Verify it's zustand (check import)
                if "zustand" in content or "create" in content:
                    results["state_management"] = "zustand"
                    # Extract state keys
                    state_keys = self._extract_zustand_state(content, zm.end())
                    results["stores"][name] = {
                        "name": name,
                        "type": "zustand",
                        "type_param": type_param,
                        "file": rel_path,
                        "state_keys": state_keys[:30],
                        "consumers": [],
                    }

            # ── Redux slices ────────────────────────────────────────
            # createSlice({ name: 'counter', initialState, reducers: {...} })
            slice_pat = re.compile(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*createSlice\s*\(\s*\{",
            )
            for sm in slice_pat.finditer(content):
                var_name = sm.group(1)
                # Extract slice name
                slice_name_match = re.search(
                    r"""name:\s*['"](\w+)['"]""",
                    content[sm.start():sm.start() + 500],
                )
                slice_name = slice_name_match.group(1) if slice_name_match else var_name
                if store_name and slice_name != store_name and var_name != store_name:
                    continue

                results["state_management"] = "redux"

                # Extract reducers
                reducers: List[str] = []
                reducer_block = re.search(
                    r"reducers:\s*\{([^}]+)\}",
                    content[sm.start():sm.start() + 2000],
                )
                if reducer_block:
                    reducers = re.findall(r"(\w+)\s*(?::\s*\(|[\(,])", reducer_block.group(1))

                # Extract extraReducers (async thunks)
                extra_reducers: List[str] = []
                extra_block = re.search(
                    r"extraReducers:\s*(?:\(builder\)\s*(?:=>)?\s*\{|{)",
                    content[sm.start():sm.start() + 5000],
                )
                if extra_block:
                    thunk_refs = re.findall(
                        r"(?:addCase|addMatcher)\s*\(\s*(\w+)",
                        content[sm.start() + extra_block.start():sm.start() + extra_block.start() + 2000],
                    )
                    extra_reducers = thunk_refs

                results["slices"][slice_name] = {
                    "name": slice_name,
                    "variable": var_name,
                    "type": "redux_slice",
                    "file": rel_path,
                    "reducers": reducers[:20],
                    "extra_reducers": extra_reducers[:10],
                    "consumers": [],
                }

            # Redux createAsyncThunk
            thunk_pat = re.compile(
                r"""(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*createAsyncThunk\s*(?:<[^>]+>)?\s*\(\s*['"]([^'"]+)['"]"""
            )
            for tp in thunk_pat.finditer(content):
                thunk_name = tp.group(1)
                thunk_type = tp.group(2)
                results["slices"][f"thunk:{thunk_name}"] = {
                    "name": thunk_name,
                    "type": "async_thunk",
                    "action_type": thunk_type,
                    "file": rel_path,
                }

            # ── MobX stores ────────────────────────────────────────
            # class Store { ... makeObservable / makeAutoObservable }
            mobx_pat = re.compile(r"make(?:Auto)?Observable\s*\(")
            if mobx_pat.search(content):
                results["state_management"] = "mobx"
                classes = re.findall(r"class\s+(\w+)", content)
                for cls_name in classes:
                    if store_name and cls_name != store_name:
                        continue
                    results["stores"][cls_name] = {
                        "name": cls_name,
                        "type": "mobx",
                        "file": rel_path,
                        "consumers": [],
                    }

            # ── Jotai atoms ─────────────────────────────────────────
            # export const countAtom = atom(0)
            # export const derivedAtom = atom((get) => get(countAtom) * 2)
            jotai_pat = re.compile(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*atom(?:<[^>]+>)?\s*\(",
            )
            for jm in jotai_pat.finditer(content):
                atom_name = jm.group(1)
                if store_name and atom_name != store_name:
                    continue
                if "jotai" in content or "atom" in content:
                    results["state_management"] = "jotai"
                    results["atoms"][atom_name] = {
                        "name": atom_name,
                        "type": "jotai_atom",
                        "file": rel_path,
                        "consumers": [],
                    }

            # ── React Context ───────────────────────────────────────
            ctx_pat = re.compile(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?createContext\s*(?:<([^>]+)>)?\s*\(",
            )
            for cm in ctx_pat.finditer(content):
                ctx_name = cm.group(1)
                ctx_type = cm.group(2)
                if store_name and ctx_name != store_name:
                    continue
                results["stores"][ctx_name] = {
                    "name": ctx_name,
                    "type": "context",
                    "type_param": ctx_type,
                    "file": rel_path,
                    "consumers": [],
                }

        # ── Second pass: find consumers ─────────────────────────────
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Zustand: useStore() or useStore(selector)
            for sname in results["stores"]:
                if results["stores"][sname]["type"] == "zustand":
                    if re.search(rf"\b{re.escape(sname)}\s*\(", content):
                        results["stores"][sname]["consumers"].append({
                            "file": rel_path,
                            "usage": "hook",
                        })

            # Redux: useSelector, useDispatch with slice actions
            if "useSelector" in content:
                selector_pat = re.compile(
                    r"useSelector\s*\(\s*(?:\([^)]*\)\s*=>)?\s*\w+\.(\w+)"
                )
                for sp in selector_pat.finditer(content):
                    slice_key = sp.group(1)
                    for slice_name, slice_info in results["slices"].items():
                        if slice_info.get("name") == slice_key or slice_key in slice_name:
                            slice_info.setdefault("consumers", []).append({
                                "file": rel_path,
                                "usage": "useSelector",
                            })

            # Jotai: useAtom(atomName)
            for atom_name in results["atoms"]:
                if re.search(rf"useAtom(?:Value)?\s*\(\s*{re.escape(atom_name)}", content):
                    results["atoms"][atom_name]["consumers"].append({
                        "file": rel_path,
                        "usage": "useAtom",
                    })

            # Context: useContext(ContextName)
            for sname in results["stores"]:
                if results["stores"][sname]["type"] == "context":
                    if re.search(rf"useContext\s*\(\s*{re.escape(sname)}", content):
                        results["stores"][sname]["consumers"].append({
                            "file": rel_path,
                            "usage": "useContext",
                        })

        results["total"] = len(results["stores"]) + len(results["slices"]) + len(results["atoms"])
        return results

    def _extract_zustand_state(self, content: str, start_pos: int) -> List[str]:
        """Extract state keys from a Zustand create() call."""
        # Find the opening brace of the state object
        depth = 0
        i = start_pos
        state_start = -1
        while i < len(content) and i < start_pos + 2000:
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
                if depth < 0:
                    break
            elif content[i] == "{" and depth <= 1:
                if state_start == -1:
                    state_start = i
                    break
            i += 1

        if state_start == -1:
            return []

        # Extract top-level keys
        block_end = min(state_start + 1000, len(content))
        block = content[state_start:block_end]
        keys: List[str] = []
        key_pat = re.compile(r"^\s*(\w+)\s*:", re.MULTILINE)
        for km in key_pat.finditer(block):
            key = km.group(1)
            if key not in ("set", "get"):
                keys.append(key)
        return keys

    # ── 5. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Screen -> navigation -> component -> hook -> API call -> store update -> re-render.

        Args:
            entry_point: Screen name or file path.
            method: Optional specific method/function to trace.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        flow: Dict[str, Any] = {
            "entry_point": entry_point,
            "steps": [],
            "warnings": [],
        }

        rn_files = self._collect_rn_files(base, ["src", "app"])
        file_contents: Dict[str, str] = {}
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if content:
                file_contents[rel_path] = content

        # Step 1: Find the entry screen/component
        target_file: Optional[str] = None
        target_content: Optional[str] = None

        for rel_path, content in file_contents.items():
            # Match by component name or file path
            if entry_point in rel_path:
                target_file = rel_path
                target_content = content
                break
            if re.search(rf"(?:function|const|let)\s+{re.escape(entry_point)}\b", content):
                target_file = rel_path
                target_content = content
                flow["steps"].append({
                    "type": "screen",
                    "name": entry_point,
                    "file": rel_path,
                })
                break

        if not target_content:
            flow["warnings"].append(f"Entry point '{entry_point}' not found")
            return flow

        # Step 2: Find navigation registration
        for rel_path, content in file_contents.items():
            if re.search(
                rf"""(?:name\s*=\s*['"]|component\s*=\s*\{{?\s*){re.escape(entry_point)}""",
                content,
            ):
                flow["steps"].append({
                    "type": "navigation",
                    "file": rel_path,
                    "detail": f"Registered as screen in navigation",
                })
                break

        # Step 3: Extract hooks used
        hooks = self._extract_hooks(target_content)
        for hook in hooks:
            step: Dict[str, Any] = {"type": "hook", "name": hook}

            # Trace custom hooks to their definitions
            if not hook.startswith("use") or hook in (
                "useState", "useEffect", "useCallback", "useMemo", "useRef",
                "useContext", "useReducer", "useLayoutEffect",
            ):
                flow["steps"].append(step)
                continue

            # Find custom hook definition
            for rel_path, content in file_contents.items():
                if re.search(
                    rf"(?:function|const|let)\s+{re.escape(hook)}\b",
                    content,
                ):
                    step["file"] = rel_path
                    # Check for API calls in hook
                    api_calls = re.findall(
                        r"""(?:fetch|axios|api)\s*[\.(]\s*['"]([^'"]+)['"]""",
                        content,
                    )
                    if api_calls:
                        step["api_calls"] = api_calls[:5]

                    # Check for store usage in hook
                    store_refs = re.findall(
                        r"(?:useSelector|useStore|useAtom(?:Value)?|useContext)\s*\(\s*(\w+)",
                        content,
                    )
                    if store_refs:
                        step["store_refs"] = store_refs[:5]
                    break

            flow["steps"].append(step)

        # Step 4: Find direct API calls
        api_pat = re.compile(
            r"""(?:fetch|axios\.(?:get|post|put|delete|patch))\s*\(\s*[`'"](.*?)[`'"]""",
        )
        for am in api_pat.finditer(target_content):
            flow["steps"].append({
                "type": "api_call",
                "url": am.group(1)[:100],
            })

        # Step 5: Find store interactions
        store_pat = re.compile(r"(?:dispatch|useSelector|useStore|useAtom)\s*\(\s*(\w+)")
        for sm in store_pat.finditer(target_content):
            flow["steps"].append({
                "type": "store",
                "name": sm.group(1),
            })

        # Step 6: Find navigation calls
        nav_pat = re.compile(
            r"""navigation\.(\w+)\s*\(\s*['"](\w+)['"]"""
        )
        for nm in nav_pat.finditer(target_content):
            flow["steps"].append({
                "type": "navigation_call",
                "action": nm.group(1),
                "target": nm.group(2),
            })

        return flow

    # ── 6. get_platform_specific ───────────────────────────────────────

    def get_platform_specific(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Find platform-specific files and code.

        Detects: .ios.tsx/.android.tsx files, Platform.select/Platform.OS conditionals.

        Args:
            file_path: Optional specific file to check. If omitted, scans all files.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "platform_files": {"ios": [], "android": [], "web": [], "native": []},
            "platform_conditionals": [],
            "platform_select_usage": [],
            "total_platform_specific": 0,
        }

        rn_files = self._collect_rn_files(base, ["src", "app", "components"])
        if file_path:
            target = os.path.join(base, file_path)
            rn_files = [(target, file_path)] if os.path.isfile(target) else []

        for fpath, rel_path in rn_files:
            # Check for platform-specific file extensions
            for platform in ["ios", "android", "web", "native"]:
                if f".{platform}." in rel_path:
                    results["platform_files"][platform].append(rel_path)
                    results["total_platform_specific"] += 1

            content = self._read_file(fpath)
            if not content:
                continue

            # Platform.OS checks
            os_pat = re.compile(r"Platform\.OS\s*===?\s*['\"](ios|android|web)['\"]")
            for om in os_pat.finditer(content):
                line_num = content[:om.start()].count("\n") + 1
                results["platform_conditionals"].append({
                    "file": rel_path,
                    "line": line_num,
                    "platform": om.group(1),
                    "text": content.split("\n")[line_num - 1].strip()[:120],
                })

            # Platform.select usage
            select_pat = re.compile(r"Platform\.select\s*\(\s*\{([^}]+)\}", re.DOTALL)
            for sm in select_pat.finditer(content):
                platforms_used = re.findall(r"(ios|android|web|default)\s*:", sm.group(1))
                line_num = content[:sm.start()].count("\n") + 1
                results["platform_select_usage"].append({
                    "file": rel_path,
                    "line": line_num,
                    "platforms": platforms_used,
                })

            # Platform.Version
            if "Platform.Version" in content:
                for vm in re.finditer(r"Platform\.Version", content):
                    line_num = content[:vm.start()].count("\n") + 1
                    results["platform_conditionals"].append({
                        "file": rel_path,
                        "line": line_num,
                        "type": "version_check",
                    })

        # Limit output
        results["platform_conditionals"] = results["platform_conditionals"][:30]
        results["platform_select_usage"] = results["platform_select_usage"][:20]

        return results

    # ── 7. get_style_map ───────────────────────────────────────────────

    def get_style_map(self, component_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse StyleSheet.create, styled-components/nativewind.
        Map style -> component usage, detect unused styles.

        Args:
            component_path: Optional specific file path to analyze.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "styling_method": "unknown",
            "style_definitions": [],
            "unused_styles": [],
            "nativewind_classes": [],
            "total": 0,
        }

        rn_files = self._collect_rn_files(base, ["src", "app"])
        if component_path:
            target = os.path.join(base, component_path)
            rn_files = [(target, component_path)] if os.path.isfile(target) else []

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── StyleSheet.create ───────────────────────────────────
            style_sheets = self._extract_stylesheet(content)
            if style_sheets:
                if results["styling_method"] == "unknown":
                    results["styling_method"] = "StyleSheet"

                for var_name, keys in style_sheets.items():
                    used_keys: List[str] = []
                    unused_keys: List[str] = []

                    for key in keys:
                        # Check if style key is used: styles.key or styles['key']
                        usage_pat = re.compile(
                            rf"""(?:{re.escape(var_name)}\.{re.escape(key)}\b|"""
                            rf"""{re.escape(var_name)}\[['\"]{re.escape(key)}['\"]\])"""
                        )
                        if usage_pat.search(content):
                            used_keys.append(key)
                        else:
                            unused_keys.append(key)

                    results["style_definitions"].append({
                        "file": rel_path,
                        "variable": var_name,
                        "keys": keys,
                        "used": used_keys,
                        "unused": unused_keys,
                    })

                    for uk in unused_keys:
                        results["unused_styles"].append({
                            "file": rel_path,
                            "variable": var_name,
                            "key": uk,
                        })

            # ── NativeWind / Tailwind CSS ───────────────────────────
            if "nativewind" in content or "className=" in content:
                if results["styling_method"] == "unknown":
                    results["styling_method"] = "nativewind"

                class_pat = re.compile(r"""className\s*=\s*(?:['"]([^'"]+)['"]|{([^}]+)})""")
                for cm in class_pat.finditer(content):
                    class_str = cm.group(1) or cm.group(2)
                    if class_str:
                        results["nativewind_classes"].append({
                            "file": rel_path,
                            "classes": class_str[:200],
                        })

            # ── styled-components ───────────────────────────────────
            if "styled." in content or "styled(" in content:
                if results["styling_method"] == "unknown":
                    results["styling_method"] = "styled-components"

            # ── Detect inline styles ────────────────────────────────
            inline_pat = re.compile(r"style\s*=\s*\{\{")
            inline_count = len(inline_pat.findall(content))
            if inline_count > 0:
                results["style_definitions"].append({
                    "file": rel_path,
                    "type": "inline",
                    "count": inline_count,
                })

        results["total"] = len(results["style_definitions"])
        return results

    # ── 8. verify_screen ───────────────────────────────────────────────

    def verify_screen(self, screen_name: str) -> Dict[str, Any]:
        """
        Verify a screen: navigation registration, component export, params typed.

        Args:
            screen_name: Screen/component name to verify.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "screen_name": screen_name,
            "checks": [],
            "issues": [],
            "status": "pass",
        }

        rn_files = self._collect_rn_files(base, ["src", "app", "navigation", "screens"])

        # Check 1: Component exists and is exported
        component_found = False
        component_file: Optional[str] = None
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue
            if re.search(
                rf"(?:export\s+(?:default\s+)?(?:function|const|let)\s+{re.escape(screen_name)}\b|"
                rf"export\s+default\s+{re.escape(screen_name)}\b|"
                rf"export\s*\{{[^}}]*\b{re.escape(screen_name)}\b[^}}]*\}})",
                content,
            ):
                component_found = True
                component_file = rel_path
                result["checks"].append({
                    "check": "component_exists",
                    "status": "pass",
                    "file": rel_path,
                })
                break

        if not component_found:
            result["issues"].append({
                "severity": "error",
                "message": f"Component '{screen_name}' not found or not exported",
            })
            result["status"] = "fail"

        # Check 2: Navigation registration
        nav_registered = False
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue
            # Check for <Stack.Screen name="ScreenName" component={ScreenName}
            if re.search(
                rf"""(?:name\s*=\s*['"]|component\s*=\s*\{{?\s*){re.escape(screen_name)}""",
                content,
            ):
                nav_registered = True
                result["checks"].append({
                    "check": "navigation_registered",
                    "status": "pass",
                    "file": rel_path,
                })
                break

        if not nav_registered:
            result["issues"].append({
                "severity": "warning",
                "message": f"Screen '{screen_name}' not found in any navigator registration",
            })

        # Check 3: Navigation params typed
        params_typed = False
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue
            # Check for type definition including this screen
            if re.search(
                rf"""(?:type\s+\w+ParamList|interface\s+\w+Params).*{re.escape(screen_name)}""",
                content, re.DOTALL,
            ):
                params_typed = True
                result["checks"].append({
                    "check": "params_typed",
                    "status": "pass",
                    "file": rel_path,
                })
                break

        if not params_typed:
            result["checks"].append({
                "check": "params_typed",
                "status": "info",
                "message": "Navigation params not found in any ParamList type definition",
            })

        if result["issues"]:
            result["status"] = "fail" if any(
                i["severity"] == "error" for i in result["issues"]
            ) else "warning"

        return result

    # ── 9. get_project_conventions ─────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real coding conventions from the React Native project.

        Args:
            pattern_type: One of: "naming", "navigation", "state", "styling", "testing", "native".
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        if pattern_type not in self.CONVENTION_PATTERNS:
            return {
                "status": "error",
                "message": f"Unknown pattern type: {pattern_type}. Use one of: {list(self.CONVENTION_PATTERNS.keys())}",
            }

        result: Dict[str, Any] = {
            "pattern_type": pattern_type,
            "description": self.CONVENTION_PATTERNS[pattern_type]["description"],
            "conventions": {},
        }

        rn_files = self._collect_rn_files(base, ["src", "app", "__tests__"])

        if pattern_type == "naming":
            result["conventions"] = self._analyze_naming_conventions(base, rn_files)
        elif pattern_type == "navigation":
            result["conventions"] = self._analyze_navigation_conventions(base, rn_files)
        elif pattern_type == "state":
            result["conventions"] = self._analyze_state_conventions(base, rn_files)
        elif pattern_type == "styling":
            result["conventions"] = self._analyze_styling_conventions(base, rn_files)
        elif pattern_type == "testing":
            result["conventions"] = self._analyze_testing_conventions(base, rn_files)
        elif pattern_type == "native":
            result["conventions"] = self._analyze_native_conventions(base, rn_files)

        return result

    def _analyze_naming_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze file and component naming conventions."""
        conventions: Dict[str, Any] = {
            "file_naming": {"PascalCase": 0, "camelCase": 0, "kebab-case": 0, "examples": []},
            "component_naming": {"PascalCase": 0, "other": 0},
            "hook_naming": {"use_prefix": 0, "examples": []},
            "screen_suffix": {"Screen": 0, "Page": 0, "View": 0, "none": 0},
        }

        for fpath, rel_path in rn_files:
            fname = os.path.basename(rel_path).split(".")[0]
            # Skip index files
            if fname == "index":
                continue

            # File naming
            if fname[0].isupper() and not "-" in fname and not "_" in fname:
                conventions["file_naming"]["PascalCase"] += 1
            elif "-" in fname:
                conventions["file_naming"]["kebab-case"] += 1
            elif fname[0].islower():
                conventions["file_naming"]["camelCase"] += 1

            if len(conventions["file_naming"]["examples"]) < 5:
                conventions["file_naming"]["examples"].append(rel_path)

            content = self._read_file(fpath)
            if not content:
                continue

            # Component naming
            comp_pat = re.compile(r"(?:function|const|let)\s+([A-Z]\w+)")
            for cm in comp_pat.finditer(content):
                comp_name = cm.group(1)
                conventions["component_naming"]["PascalCase"] += 1

                # Screen suffix
                if comp_name.endswith("Screen"):
                    conventions["screen_suffix"]["Screen"] += 1
                elif comp_name.endswith("Page"):
                    conventions["screen_suffix"]["Page"] += 1
                elif comp_name.endswith("View"):
                    conventions["screen_suffix"]["View"] += 1

            # Hook naming
            hook_pat = re.compile(r"(?:function|const|let)\s+(use[A-Z]\w+)")
            for hm in hook_pat.finditer(content):
                conventions["hook_naming"]["use_prefix"] += 1
                if len(conventions["hook_naming"]["examples"]) < 5:
                    conventions["hook_naming"]["examples"].append(hm.group(1))

        # Determine dominant
        fn = conventions["file_naming"]
        dominant = max(
            ("PascalCase", fn["PascalCase"]),
            ("camelCase", fn["camelCase"]),
            ("kebab-case", fn["kebab-case"]),
            key=lambda x: x[1],
        )
        conventions["file_naming"]["dominant"] = dominant[0]

        return conventions

    def _analyze_navigation_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze navigation structure conventions."""
        conventions: Dict[str, Any] = {
            "library": "unknown",
            "navigators": {"stack": 0, "tab": 0, "drawer": 0, "native_stack": 0},
            "deep_linking": False,
            "typed_params": False,
            "navigation_files": [],
        }

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if "@react-navigation" in content:
                conventions["library"] = "react-navigation"

            # Count navigator types
            if "createStackNavigator" in content:
                conventions["navigators"]["stack"] += 1
            if "createNativeStackNavigator" in content:
                conventions["navigators"]["native_stack"] += 1
            if "createBottomTabNavigator" in content or "createMaterialTopTabNavigator" in content:
                conventions["navigators"]["tab"] += 1
            if "createDrawerNavigator" in content:
                conventions["navigators"]["drawer"] += 1

            # Deep linking
            if "linking" in content and "prefixes" in content:
                conventions["deep_linking"] = True

            # Typed params
            if re.search(r"type\s+\w+ParamList", content):
                conventions["typed_params"] = True

            # Navigation files
            if "navigation" in rel_path.lower() or "Navigator" in content:
                if len(conventions["navigation_files"]) < 10:
                    conventions["navigation_files"].append(rel_path)

        return conventions

    def _analyze_state_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze state management conventions."""
        conventions: Dict[str, Any] = {
            "frameworks": [],
            "redux": {"toolkit": False, "thunks": 0, "slices": 0},
            "zustand": {"stores": 0},
            "mobx": {"stores": 0},
            "jotai": {"atoms": 0},
            "react_query": {"queries": 0},
            "context": {"providers": 0},
        }

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Redux
            if "@reduxjs/toolkit" in content or "react-redux" in content:
                if "redux" not in conventions["frameworks"]:
                    conventions["frameworks"].append("redux")
                conventions["redux"]["toolkit"] = "@reduxjs/toolkit" in content
                conventions["redux"]["slices"] += len(re.findall(r"createSlice\s*\(", content))
                conventions["redux"]["thunks"] += len(re.findall(r"createAsyncThunk\s*\(", content))

            # Zustand
            if "zustand" in content:
                if "zustand" not in conventions["frameworks"]:
                    conventions["frameworks"].append("zustand")
                conventions["zustand"]["stores"] += len(re.findall(r"create\s*(?:<[^>]+>)?\s*\(", content))

            # MobX
            if "mobx" in content:
                if "mobx" not in conventions["frameworks"]:
                    conventions["frameworks"].append("mobx")
                conventions["mobx"]["stores"] += len(re.findall(r"makeAutoObservable\s*\(", content))

            # Jotai
            if "jotai" in content:
                if "jotai" not in conventions["frameworks"]:
                    conventions["frameworks"].append("jotai")
                conventions["jotai"]["atoms"] += len(re.findall(r"\batom\s*\(", content))

            # React Query / TanStack Query
            if "@tanstack/react-query" in content or "react-query" in content:
                if "react-query" not in conventions["frameworks"]:
                    conventions["frameworks"].append("react-query")
                conventions["react_query"]["queries"] += len(
                    re.findall(r"useQuery\s*\(|useMutation\s*\(", content)
                )

            # Context
            if "createContext" in content:
                conventions["context"]["providers"] += 1

        return conventions

    def _analyze_styling_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze styling conventions."""
        conventions: Dict[str, Any] = {
            "primary_method": "unknown",
            "stylesheet_count": 0,
            "nativewind_count": 0,
            "styled_components_count": 0,
            "inline_style_count": 0,
            "theme_detected": False,
        }

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if "StyleSheet.create" in content:
                conventions["stylesheet_count"] += 1
            if "className=" in content:
                conventions["nativewind_count"] += 1
            if "styled." in content or "styled(" in content:
                conventions["styled_components_count"] += 1
            conventions["inline_style_count"] += len(re.findall(r"style\s*=\s*\{\{", content))

            if "ThemeProvider" in content or "useTheme" in content:
                conventions["theme_detected"] = True

        # Determine primary
        counts = {
            "StyleSheet": conventions["stylesheet_count"],
            "NativeWind": conventions["nativewind_count"],
            "styled-components": conventions["styled_components_count"],
        }
        if any(v > 0 for v in counts.values()):
            conventions["primary_method"] = max(counts, key=counts.get)

        return conventions

    def _analyze_testing_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze testing conventions."""
        conventions: Dict[str, Any] = {
            "frameworks": [],
            "jest_tests": 0,
            "rtl_tests": 0,
            "detox_tests": 0,
            "maestro_tests": 0,
            "test_files": [],
        }

        # Check for test config
        pkg_json_path = os.path.join(base, "package.json")
        pkg_content = self._read_file(pkg_json_path)
        if pkg_content:
            try:
                pkg = json.loads(pkg_content)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "jest" in deps:
                    conventions["frameworks"].append("jest")
                if "@testing-library/react-native" in deps:
                    conventions["frameworks"].append("testing-library")
                if "detox" in deps:
                    conventions["frameworks"].append("detox")
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Check for maestro flows
        maestro_dir = os.path.join(base, ".maestro")
        if os.path.isdir(maestro_dir):
            conventions["frameworks"].append("maestro")
            for fname in os.listdir(maestro_dir):
                if fname.endswith((".yaml", ".yml")):
                    conventions["maestro_tests"] += 1

        # Scan test files
        test_dirs = ["__tests__", "src/__tests__", "tests", "test"]
        for test_dir in test_dirs:
            full_dir = os.path.join(base, test_dir)
            if not os.path.isdir(full_dir):
                continue
            for root, _, files in os.walk(full_dir):
                for fname in files:
                    if fname.endswith((".test.tsx", ".test.ts", ".test.jsx", ".test.js",
                                       ".spec.tsx", ".spec.ts")):
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, base)
                        content = self._read_file(fpath)
                        if not content:
                            continue

                        conventions["jest_tests"] += 1
                        if "render(" in content or "screen." in content:
                            conventions["rtl_tests"] += 1

                        if len(conventions["test_files"]) < 10:
                            conventions["test_files"].append(rel)

        # Detox tests
        e2e_dir = os.path.join(base, "e2e")
        if os.path.isdir(e2e_dir):
            for root, _, files in os.walk(e2e_dir):
                for fname in files:
                    if fname.endswith((".test.ts", ".test.js", ".e2e.ts", ".e2e.js")):
                        conventions["detox_tests"] += 1

        return conventions

    def _analyze_native_conventions(
        self, base: str, rn_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze native bridge conventions."""
        conventions: Dict[str, Any] = {
            "architecture": "unknown",
            "turbo_modules": 0,
            "old_bridge": 0,
            "expo_modules": 0,
            "fabric_components": 0,
            "native_files": {"ios": 0, "android": 0},
        }

        # Check for new architecture
        gradle_path = os.path.join(base, "android", "gradle.properties")
        gradle_content = self._read_file(gradle_path)
        if gradle_content:
            if "newArchEnabled=true" in gradle_content:
                conventions["architecture"] = "new_architecture"
            else:
                conventions["architecture"] = "old_architecture"

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if "TurboModuleRegistry" in content:
                conventions["turbo_modules"] += 1
            if "NativeModules." in content:
                conventions["old_bridge"] += 1
            if "codegenNativeComponent" in content:
                conventions["fabric_components"] += 1

        # Check expo
        app_json = os.path.join(base, "app.json")
        app_content = self._read_file(app_json)
        if app_content:
            try:
                config = json.loads(app_content)
                if "expo" in config:
                    conventions["architecture"] = "expo"
                    plugins = config.get("expo", {}).get("plugins", [])
                    conventions["expo_modules"] = len(plugins)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Count native files
        for platform in ["ios", "android"]:
            platform_dir = os.path.join(base, platform)
            if os.path.isdir(platform_dir):
                for root, _, files in os.walk(platform_dir):
                    for fname in files:
                        if fname.endswith((".m", ".mm", ".swift", ".java", ".kt")):
                            conventions["native_files"][platform] += 1

        return conventions

    # ── 10. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.

        Args:
            description: Natural language description (e.g., "modal dialog", "FlatList pagination").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {"query": description, "matches": [], "total": 0}

        desc_lower = description.lower()
        matched_patterns: List[Tuple[str, Dict[str, Any]]] = []
        for pattern_name, pattern_info in self.SIMILAR_PATTERN_REGISTRY.items():
            score = sum(1 for kw in pattern_info["keywords"] if kw in desc_lower)
            if score > 0:
                matched_patterns.append((pattern_name, pattern_info))

        if not matched_patterns:
            matched_patterns = list(self.SIMILAR_PATTERN_REGISTRY.items())

        rn_files = self._collect_rn_files(base, ["src", "app"])

        for pattern_name, pattern_info in matched_patterns:
            file_matches: List[Dict[str, Any]] = []
            for fpath, rel_path in rn_files:
                content = self._read_file(fpath)
                if not content:
                    continue

                for fp in pattern_info["file_patterns"]:
                    matches = re.findall(fp, content)
                    if matches:
                        lines = content.split("\n")
                        match_lines: List[Dict[str, Any]] = []
                        for i, line in enumerate(lines, 1):
                            if re.search(fp, line):
                                match_lines.append({
                                    "line": i,
                                    "text": line.strip()[:120],
                                })
                        file_matches.append({
                            "file": rel_path,
                            "pattern": fp,
                            "count": len(matches),
                            "lines": match_lines[:5],
                        })
                        break

            if file_matches:
                results["matches"].append({
                    "pattern": pattern_name,
                    "description": pattern_info["description"],
                    "files": file_matches[:10],
                })

        results["total"] = sum(len(m["files"]) for m in results["matches"])
        return results

    # ── 11. get_project_snapshot ───────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a compact snapshot of the React Native project.

        Returns: screens, navigation structure, stores, native modules,
        platform-specific files, Expo config, dependencies.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "project_name": "unknown",
            "is_expo": False,
            "screens": [],
            "components_count": 0,
            "navigation": {"navigators": 0, "screens": 0},
            "state_management": [],
            "native_modules": 0,
            "platform_specific_files": 0,
            "dependencies": {},
            "dev_dependencies": {},
            "scripts": {},
            "directory_structure": {},
            "file_count": 0,
            "test_count": 0,
        }

        # Parse package.json
        pkg_json_path = os.path.join(base, "package.json")
        pkg_content = self._read_file(pkg_json_path)
        if pkg_content:
            try:
                pkg = json.loads(pkg_content)
                snapshot["project_name"] = pkg.get("name", "unknown")
                snapshot["dependencies"] = {
                    k: str(v) for k, v in list(pkg.get("dependencies", {}).items())[:30]
                }
                snapshot["dev_dependencies"] = {
                    k: str(v) for k, v in list(pkg.get("devDependencies", {}).items())[:20]
                }
                snapshot["scripts"] = pkg.get("scripts", {})

                # Detect state management from deps
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                for lib in ["react-redux", "zustand", "mobx-react", "jotai",
                            "@tanstack/react-query", "recoil"]:
                    if lib in deps:
                        snapshot["state_management"].append(lib)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Check Expo
        app_json = os.path.join(base, "app.json")
        app_content = self._read_file(app_json)
        if app_content:
            try:
                config = json.loads(app_content)
                if "expo" in config:
                    snapshot["is_expo"] = True
                    snapshot["expo_sdk"] = config.get("expo", {}).get("sdkVersion")
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Directory structure
        src_path = os.path.join(base, "src")
        if os.path.isdir(src_path):
            for item in sorted(os.listdir(src_path)):
                item_path = os.path.join(src_path, item)
                if os.path.isdir(item_path):
                    count = sum(
                        1 for _, _, files in os.walk(item_path)
                        for f in files if f.endswith((".tsx", ".ts", ".jsx", ".js"))
                    )
                    snapshot["directory_structure"][item] = count

        # Scan files
        rn_files = self._collect_rn_files(base, ["src", "app"])
        snapshot["file_count"] = len(rn_files)

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Count components
            comp_pat = re.compile(
                r"(?:export\s+(?:default\s+)?)?(?:function|const|let)\s+([A-Z]\w+)"
            )
            for cm in comp_pat.finditer(content):
                comp_name = cm.group(1)
                snapshot["components_count"] += 1
                if comp_name.endswith(("Screen", "Page")):
                    snapshot["screens"].append({
                        "name": comp_name,
                        "file": rel_path,
                    })

            # Count navigators
            if re.search(r"create\w+Navigator\s*\(", content):
                snapshot["navigation"]["navigators"] += 1
            nav_screen_count = len(re.findall(r"""\.Screen\s+name=""", content))
            snapshot["navigation"]["screens"] += nav_screen_count

            # Count native module usage
            if "NativeModules" in content or "TurboModuleRegistry" in content:
                snapshot["native_modules"] += 1

            # Platform-specific
            for platform in [".ios.", ".android.", ".web."]:
                if platform in rel_path:
                    snapshot["platform_specific_files"] += 1

        # Count tests
        test_files = self._collect_rn_files(base, ["__tests__", "tests", "test"])
        snapshot["test_count"] = len(test_files)

        # Limit screens
        snapshot["screens"] = snapshot["screens"][:30]

        return snapshot

    # ── 12. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit impact check for React Native files.

        screen -> check navigation
        component -> check consumers
        native module -> check platform compatibility

        Args:
            file_path: Relative file path.
            symbol_name: Symbol to check.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "file_path": file_path,
            "symbol_name": symbol_name,
            "symbol_type": "unknown",
            "impact": [],
            "warnings": [],
            "references": [],
        }

        target_path = self._resolve_rn_file_path(base, file_path)
        if not target_path:
            return {"status": "error", "message": f"File not found: {file_path}"}
        target_content = self._read_file(target_path)
        if not target_content:
            return {"status": "error", "message": f"File not found: {file_path}"}

        rn_files = self._collect_rn_files(base, ["src", "app", "__tests__"])

        # Determine symbol type
        # Screen/Component?
        is_component = bool(re.search(
            rf"(?:export\s+(?:default\s+)?)?(?:function|const|let)\s+{re.escape(symbol_name)}\b",
            target_content,
        ))

        if is_component:
            is_screen = symbol_name.endswith(("Screen", "Page"))
            result["symbol_type"] = "screen" if is_screen else "component"
            self._check_component_impact(symbol_name, is_screen, rn_files, base, result)
            return result

        # Hook?
        is_hook = bool(re.search(
            rf"(?:function|const|let)\s+{re.escape(symbol_name)}\b",
            target_content,
        )) and symbol_name.startswith("use")

        if is_hook:
            result["symbol_type"] = "hook"
            self._check_hook_impact(symbol_name, rn_files, base, result)
            return result

        # Store?
        is_store = bool(re.search(
            rf"(?:const|let)\s+{re.escape(symbol_name)}\s*=\s*(?:create|createSlice|atom|createContext)",
            target_content,
        ))
        if is_store:
            result["symbol_type"] = "store"
            self._check_store_impact(symbol_name, rn_files, base, result)
            return result

        # Native module?
        is_native = bool(re.search(
            rf"(?:NativeModules|TurboModuleRegistry).*{re.escape(symbol_name)}",
            target_content,
        ))
        if is_native:
            result["symbol_type"] = "native_module"
            self._check_native_impact(symbol_name, rn_files, base, result)
            return result

        # Generic - find all references
        result["symbol_type"] = "generic"
        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content or symbol_name not in content:
                continue
            lines = content.split("\n")
            refs: List[Dict[str, Any]] = []
            for i, line in enumerate(lines, 1):
                if symbol_name in line:
                    refs.append({"line": i, "text": line.strip()[:120]})
            if refs:
                result["references"].append({
                    "file": rel_path,
                    "usages": refs[:5],
                })

        return result

    def _check_component_impact(
        self, comp_name: str, is_screen: bool,
        rn_files: List[Tuple[str, str]], base: str,
        result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a component/screen."""
        importers: List[Dict[str, Any]] = []
        jsx_users: List[Dict[str, Any]] = []
        nav_refs: List[Dict[str, Any]] = []

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content or comp_name not in content:
                continue

            # Import references
            if re.search(
                rf"""import\s+.*\b{re.escape(comp_name)}\b.*from""",
                content,
            ):
                importers.append({"file": rel_path, "type": "import"})

            # JSX usage
            if re.search(rf"<{re.escape(comp_name)}\b", content):
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if re.search(rf"<{re.escape(comp_name)}\b", line):
                        jsx_users.append({
                            "file": rel_path,
                            "line": i,
                            "text": line.strip()[:120],
                        })

            # Navigation references
            if re.search(
                rf"""(?:name\s*=\s*['"]|component\s*=\s*\{{?\s*|navigate\s*\(\s*['"]){re.escape(comp_name)}""",
                content,
            ):
                nav_refs.append({"file": rel_path})

        result["impact"].append({
            "type": "importers",
            "count": len(importers),
            "items": importers[:15],
        })
        result["impact"].append({
            "type": "jsx_usage",
            "count": len(jsx_users),
            "items": jsx_users[:15],
        })

        if is_screen and nav_refs:
            result["impact"].append({
                "type": "navigation_references",
                "count": len(nav_refs),
                "items": nav_refs[:10],
            })
            result["warnings"].append(
                f"Screen '{comp_name}' is referenced in navigation. "
                "Renaming/removing will break navigation."
            )

        if len(jsx_users) > 5:
            result["warnings"].append(
                f"Component '{comp_name}' is used in {len(jsx_users)} places. "
                "Prop changes will propagate widely."
            )

    def _check_hook_impact(
        self, hook_name: str, rn_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a custom hook."""
        consumers: List[Dict[str, Any]] = []

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content or hook_name not in content:
                continue

            if re.search(rf"\b{re.escape(hook_name)}\s*\(", content):
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if re.search(rf"\b{re.escape(hook_name)}\s*\(", line):
                        consumers.append({
                            "file": rel_path,
                            "line": i,
                            "text": line.strip()[:120],
                        })

        result["impact"].append({
            "type": "consumers",
            "count": len(consumers),
            "items": consumers[:20],
        })

        if len(consumers) > 3:
            result["warnings"].append(
                f"Hook '{hook_name}' is used in {len(consumers)} places. "
                "Return value changes will affect all consumers."
            )

    def _check_store_impact(
        self, store_name: str, rn_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a store."""
        consumers: List[Dict[str, Any]] = []

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content or store_name not in content:
                continue

            # Direct usage, useSelector, useAtom
            patterns = [
                rf"\b{re.escape(store_name)}\s*\(",
                rf"useSelector\s*\([^)]*{re.escape(store_name)}",
                rf"useAtom(?:Value)?\s*\(\s*{re.escape(store_name)}",
                rf"useContext\s*\(\s*{re.escape(store_name)}",
            ]
            for pat in patterns:
                for m in re.finditer(pat, content):
                    line_num = content[:m.start()].count("\n") + 1
                    consumers.append({
                        "file": rel_path,
                        "line": line_num,
                    })

        result["impact"].append({
            "type": "consumers",
            "count": len(consumers),
            "items": consumers[:20],
        })

        if len(consumers) > 3:
            result["warnings"].append(
                f"Store '{store_name}' has {len(consumers)} consumers. "
                "State shape changes will propagate to all."
            )

    def _check_native_impact(
        self, module_name: str, rn_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a native module."""
        js_refs: List[Dict[str, Any]] = []
        native_files: List[Dict[str, Any]] = []

        for fpath, rel_path in rn_files:
            content = self._read_file(fpath)
            if not content or module_name not in content:
                continue
            js_refs.append({"file": rel_path})

        # Check native files
        for platform in ["ios", "android"]:
            platform_dir = os.path.join(base, platform)
            if not os.path.isdir(platform_dir):
                continue
            for root, _, files in os.walk(platform_dir):
                for fname in files:
                    if fname.endswith((".m", ".mm", ".swift", ".java", ".kt")):
                        fpath = os.path.join(root, fname)
                        content = self._read_file(fpath)
                        if content and module_name in content:
                            native_files.append({
                                "file": os.path.relpath(fpath, base),
                                "platform": platform,
                            })

        result["impact"].append({
            "type": "js_references",
            "count": len(js_refs),
            "items": js_refs[:10],
        })
        result["impact"].append({
            "type": "native_implementations",
            "count": len(native_files),
            "items": native_files[:10],
        })

        if native_files:
            platforms = set(nf["platform"] for nf in native_files)
            result["warnings"].append(
                f"Native module '{module_name}' has implementations on: {', '.join(platforms)}. "
                "Interface changes require updating both JS and native code."
            )
        else:
            result["warnings"].append(
                f"No native implementation files found for module '{module_name}'. "
                "Ensure native bridge code exists."
            )

    # ── verify_schema ─────────────────────────────────────────────────
    def verify_schema(self, entity_or_model: str, fields: list) -> Dict[str, Any]:
        """Check TypeScript interfaces/types for API response models and AsyncStorage keys."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        result: Dict[str, Any] = {
            "model": entity_or_model,
            "requested_fields": fields,
            "interface_fields": [],
            "type_alias_fields": [],
            "async_storage_keys": [],
            "missing_fields": [],
            "warnings": [],
        }

        found_fields: Set[str] = set()

        ts_extensions = (".ts", ".tsx", ".d.ts")
        for root, _, files in os.walk(base):
            if "node_modules" in root or ".git" in root:
                continue
            for fname in files:
                if not any(fname.endswith(ext) for ext in ts_extensions):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue

                if entity_or_model not in content:
                    continue

                rel = os.path.relpath(fpath, base)

                # Match interface fields: interface Model { field: type; }
                iface_re = re.compile(
                    r'(?:export\s+)?interface\s+' + re.escape(entity_or_model)
                    + r'\s*(?:extends\s+[^{]*)?\{([^}]*)\}',
                    re.DOTALL,
                )
                iface_match = iface_re.search(content)
                if iface_match:
                    body = iface_match.group(1)
                    field_re = re.compile(r'(\w+)\s*[?]?\s*:\s*([^;]+);')
                    for fm in field_re.finditer(body):
                        field_name = fm.group(1)
                        field_type = fm.group(2).strip()
                        result["interface_fields"].append({
                            "file": rel, "field": field_name, "type": field_type,
                        })
                        found_fields.add(field_name)

                # Match type alias fields: type Model = { field: type; }
                type_re = re.compile(
                    r'(?:export\s+)?type\s+' + re.escape(entity_or_model)
                    + r'\s*=\s*\{([^}]*)\}',
                    re.DOTALL,
                )
                type_match = type_re.search(content)
                if type_match:
                    body = type_match.group(1)
                    field_re = re.compile(r'(\w+)\s*[?]?\s*:\s*([^;]+);')
                    for fm in field_re.finditer(body):
                        field_name = fm.group(1)
                        field_type = fm.group(2).strip()
                        result["type_alias_fields"].append({
                            "file": rel, "field": field_name, "type": field_type,
                        })
                        found_fields.add(field_name)

                # AsyncStorage key references for this model
                storage_re = re.compile(
                    r'AsyncStorage\.(?:getItem|setItem|removeItem)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]'
                )
                for sm in storage_re.finditer(content):
                    key = sm.group(1)
                    if entity_or_model.lower() in key.lower():
                        result["async_storage_keys"].append({
                            "file": rel, "key": key,
                        })

        for field in fields:
            if field not in found_fields:
                result["missing_fields"].append(field)

        if result["missing_fields"]:
            result["warnings"].append(
                f"Fields not found in any interface/type: {', '.join(result['missing_fields'])}"
            )

        return {"status": "ok", **result}

    # ── detect_transaction_risks ──────────────────────────────────────
    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing error handling around AsyncStorage batch ops and API calls without retry."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result: Dict[str, Any] = {
            "file": file_path,
            "risks": [],
            "warnings": [],
            "async_storage_ops": 0,
            "api_calls": 0,
        }

        lines = content.split("\n")

        # Detect AsyncStorage operations
        storage_re = re.compile(r'AsyncStorage\.(?:multiSet|multiGet|multiRemove|multiMerge)\s*\(')
        storage_single_re = re.compile(r'AsyncStorage\.(?:setItem|getItem|removeItem|mergeItem)\s*\(')
        storage_locs = []
        for i, line in enumerate(lines):
            if storage_re.search(line) or storage_single_re.search(line):
                storage_locs.append((i + 1, line.strip()))
        result["async_storage_ops"] = len(storage_locs)

        # Check if AsyncStorage batch ops are inside try/catch
        batch_ops = re.compile(r'AsyncStorage\.multi(?:Set|Get|Remove|Merge)\s*\(')
        for i, line in enumerate(lines):
            if batch_ops.search(line):
                # Look backward for try block
                has_try = False
                for j in range(max(0, i - 10), i):
                    if re.search(r'\btry\s*\{', lines[j]):
                        has_try = True
                        break
                if not has_try:
                    result["risks"].append({
                        "type": "unhandled_batch_storage",
                        "severity": "error",
                        "message": "AsyncStorage batch operation without try/catch",
                        "line": i + 1,
                        "code": line.strip(),
                    })

        # Detect API calls without retry/offline support
        api_re = re.compile(r'(?:fetch|axios|api)\s*[\.(]')
        api_locs = [(i + 1, ln.strip()) for i, ln in enumerate(lines) if api_re.search(ln)]
        result["api_calls"] = len(api_locs)

        has_retry = bool(re.search(r'retry|retries|maxRetries|retryCount|withRetry', content))
        has_offline = bool(re.search(r'NetInfo|isConnected|isOnline|offline|connectivity', content))

        if api_locs and not has_retry:
            result["risks"].append({
                "type": "api_no_retry",
                "severity": "warning",
                "message": f"Found {len(api_locs)} API calls without retry mechanism",
                "locations": [{"line": loc[0], "code": loc[1]} for loc in api_locs[:5]],
            })

        if api_locs and not has_offline:
            result["risks"].append({
                "type": "api_no_offline_support",
                "severity": "warning",
                "message": "API calls detected without offline/connectivity check",
            })

        # Multiple sequential AsyncStorage calls without batching
        consecutive_storage = 0
        for i, line in enumerate(lines):
            if storage_single_re.search(line):
                consecutive_storage += 1
            else:
                if consecutive_storage >= 3:
                    result["risks"].append({
                        "type": "unbatched_storage_ops",
                        "severity": "warning",
                        "message": f"{consecutive_storage} sequential AsyncStorage calls -- use multiSet/multiGet instead",
                        "line": i + 1 - consecutive_storage,
                    })
                consecutive_storage = 0

        if not result["risks"]:
            result["warnings"].append("No transaction risks detected")

        return {"status": "ok", **result}

    # ── get_domain_rules ──────────────────────────────────────────────
    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check RN domain rules: screen typing, component storage, hooks, native modules."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result: Dict[str, Any] = {
            "file": file_path,
            "violations": [],
            "suggestions": [],
            "file_type": "unknown",
        }

        rel_lower = file_path.lower().replace("\\", "/")

        is_screen = "screen" in rel_lower or bool(re.search(r'Screen\b', os.path.basename(file_path)))
        is_component = ("component" in rel_lower) and not is_screen
        is_hook = "hook" in rel_lower or bool(re.search(r'^use[A-Z]', os.path.basename(file_path)))
        is_native_module = "native" in rel_lower or bool(re.search(r'NativeModules', content))

        if is_screen:
            result["file_type"] = "screen"
            # Screens should have navigation typing
            has_nav_typing = bool(re.search(
                r'(?:StackScreenProps|NativeStackScreenProps|ScreenProps|NavigationProp|RouteProp)\s*<', content
            ))
            has_route_params = bool(re.search(r'route\.params', content))
            if not has_nav_typing and has_route_params:
                result["violations"].append({
                    "rule": "screen_requires_navigation_typing",
                    "severity": "warning",
                    "message": "Screen uses route.params without typed navigation props",
                })
            elif not has_nav_typing:
                result["suggestions"].append(
                    "Consider adding typed navigation props (StackScreenProps<RootStackParamList>)"
                )

        if is_component:
            result["file_type"] = "component"
            # Components should not directly use AsyncStorage
            if re.search(r'AsyncStorage', content):
                result["violations"].append({
                    "rule": "no_direct_async_storage_in_component",
                    "severity": "warning",
                    "message": "Component directly uses AsyncStorage -- move storage logic to a hook or service",
                })

        if is_hook:
            result["file_type"] = "hook"
            # Hooks with async operations should have error boundaries
            has_async = bool(re.search(r'async\s|\.then\s*\(|await\s', content))
            has_error_handling = bool(re.search(r'catch\s*\(|\.catch\s*\(|onError|setError|error\s*,', content))
            if has_async and not has_error_handling:
                result["violations"].append({
                    "rule": "hook_requires_error_handling",
                    "severity": "warning",
                    "message": "Hook has async operations without error handling",
                })

        if is_native_module:
            result["file_type"] = "native_module"
            # Native modules should have platform checks
            has_platform_check = bool(re.search(r'Platform\.(?:OS|select|Version)', content))
            if not has_platform_check:
                result["violations"].append({
                    "rule": "native_module_requires_platform_check",
                    "severity": "warning",
                    "message": "Native module usage without Platform.OS check -- may crash on unsupported platform",
                })

        return {"status": "ok", **result}

    # ── generate_test_skeleton ────────────────────────────────────────
    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Generate Jest + @testing-library/react-native test skeleton."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol,
            "test_code": "",
            "test_type": "component",
            "dependencies": [],
        }

        # Detect if it's a component, hook, or utility
        is_hook = symbol.startswith("use") and symbol[3:4].isupper()
        is_component = bool(re.search(
            r'(?:export\s+(?:default\s+)?(?:function|const)\s+' + re.escape(symbol) + r'|'
            r'class\s+' + re.escape(symbol) + r'\s+extends\s+(?:React\.)?(?:Component|PureComponent))',
            content,
        ))

        # Extract props interface
        props_re = re.compile(
            r'(?:interface|type)\s+' + re.escape(symbol) + r'Props\s*[={]\s*\{?([^}]*)\}',
            re.DOTALL,
        )
        props_match = props_re.search(content)
        mock_props = ""
        if props_match:
            body = props_match.group(1)
            prop_fields = re.findall(r'(\w+)\s*[?]?\s*:\s*([^;]+);', body)
            for pname, ptype in prop_fields:
                ptype = ptype.strip()
                if "string" in ptype:
                    mock_props += f"    {pname}: 'test-{pname}',\n"
                elif "number" in ptype:
                    mock_props += f"    {pname}: 1,\n"
                elif "boolean" in ptype:
                    mock_props += f"    {pname}: false,\n"
                elif "()" in ptype or "=>" in ptype:
                    mock_props += f"    {pname}: jest.fn(),\n"
                else:
                    mock_props += f"    {pname}: undefined as any,\n"

        # Detect navigation usage
        uses_navigation = bool(re.search(r'useNavigation|navigation\.navigate', content))
        nav_mock = ""
        if uses_navigation:
            nav_mock = """
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({
    navigate: jest.fn(),
    goBack: jest.fn(),
  }),
  useRoute: () => ({
    params: {},
  }),
}));
"""
            result["dependencies"].append("@react-navigation/native")

        import_path = file_path.replace(".tsx", "").replace(".ts", "").replace(".jsx", "").replace(".js", "")
        import_path = f"../{import_path}" if not import_path.startswith(".") else import_path

        if is_hook:
            result["test_type"] = "hook"
            result["test_code"] = f"""import {{ renderHook, act, waitFor }} from '@testing-library/react-native';
import {{ {symbol} }} from '{import_path}';
{nav_mock}
describe('{symbol}', () => {{
  it('should return initial state', () => {{
    const {{ result }} = renderHook(() => {symbol}());
    expect(result.current).toBeDefined();
  }});

  it('should handle updates', async () => {{
    const {{ result }} = renderHook(() => {symbol}());

    await act(async () => {{
      // trigger state change
    }});

    await waitFor(() => {{
      expect(result.current).toBeDefined();
    }});
  }});

  it('should handle errors gracefully', async () => {{
    const {{ result }} = renderHook(() => {symbol}());
    // Verify error state
    expect(result.current).toBeDefined();
  }});
}});
"""
        elif is_component:
            result["test_type"] = "component"
            props_block = f"""const defaultProps = {{
{mock_props}}};
""" if mock_props else ""
            props_arg = "{{...defaultProps}}" if mock_props else ""
            result["test_code"] = f"""import React from 'react';
import {{ render, fireEvent, waitFor }} from '@testing-library/react-native';
import {{ {symbol} }} from '{import_path}';
{nav_mock}
{props_block}
describe('{symbol}', () => {{
  it('should render correctly', () => {{
    const {{ toJSON }} = render(<{symbol} {props_arg} />);
    expect(toJSON()).toBeTruthy();
  }});

  it('should handle user interaction', () => {{
    const {{ getByTestId }} = render(<{symbol} {props_arg} />);
    // fireEvent.press(getByTestId('button-id'));
  }});

  it('should update on prop changes', async () => {{
    const {{ rerender }} = render(<{symbol} {props_arg} />);
    // rerender(<{symbol} {{...defaultProps, someProp: 'new'}} />);
    await waitFor(() => {{
      // assert new state
    }});
  }});
}});
"""
        else:
            result["test_type"] = "utility"
            result["test_code"] = f"""import {{ {symbol} }} from '{import_path}';

describe('{symbol}', () => {{
  it('should work with valid input', () => {{
    const result = {symbol}();
    expect(result).toBeDefined();
  }});

  it('should handle edge cases', () => {{
    expect(() => {symbol}()).not.toThrow();
  }});
}});
"""

        return {"status": "ok", **result}

    # ── match_view_guards ─────────────────────────────────────────────
    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match navigation auth guards with conditional screen renders and auth state."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol,
            "auth_guards": [],
            "conditional_renders": [],
            "auth_providers": [],
            "mismatches": [],
            "warnings": [],
        }

        # Detect navigation auth guards
        nav_guard_patterns = [
            (r'isAuthenticated\s*\?\s*', "isAuthenticated_ternary"),
            (r'isLoggedIn\s*\?\s*', "isLoggedIn_ternary"),
            (r'(?:user|token|auth)\s*\?\s*<\s*\w+\.Navigator', "auth_navigator_guard"),
            (r'if\s*\(\s*!(?:isAuthenticated|isLoggedIn|user|token)', "auth_redirect_guard"),
        ]
        for pattern, guard_type in nav_guard_patterns:
            matches = list(re.finditer(pattern, content))
            for m in matches:
                line_no = content[:m.start()].count("\n") + 1
                result["auth_guards"].append({
                    "type": guard_type, "line": line_no,
                })

        # Detect conditional screen rendering
        cond_screen_re = re.compile(
            r'(?:isAuthenticated|isLoggedIn|user|token|auth)\s*(?:&&|\?)\s*.*?<\s*(?:Stack|Tab|Drawer)\.Screen',
            re.DOTALL,
        )
        for m in cond_screen_re.finditer(content):
            line_no = content[:m.start()].count("\n") + 1
            result["conditional_renders"].append({
                "type": "conditional_screen", "line": line_no,
                "code": m.group(0)[:80],
            })

        # Scan for auth context/provider patterns across project
        auth_state_vars: Set[str] = set()
        src_dir = os.path.join(base, "src")
        scan_dirs = [src_dir] if os.path.isdir(src_dir) else [base]
        for scan_dir in scan_dirs:
            for root, _, files in os.walk(scan_dir):
                if "node_modules" in root:
                    continue
                for fname in files:
                    if not fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            fcontent = f.read()
                    except Exception:
                        continue

                    # Auth provider/context definitions
                    provider_re = re.compile(
                        r'(?:export\s+)?(?:const|function)\s+(Auth\w*(?:Provider|Context))',
                    )
                    for pm in provider_re.finditer(fcontent):
                        result["auth_providers"].append({
                            "name": pm.group(1),
                            "file": os.path.relpath(fpath, base),
                        })

                    # Auth state variables
                    auth_var_re = re.compile(
                        r'(?:const|let)\s+\[?\s*(isAuthenticated|isLoggedIn|authState|user|token)',
                    )
                    for av in auth_var_re.finditer(fcontent):
                        auth_state_vars.add(av.group(1))

        # Check for mismatches
        has_guard = len(result["auth_guards"]) > 0
        has_conditional = len(result["conditional_renders"]) > 0
        has_provider = len(result["auth_providers"]) > 0

        if has_guard and not has_provider:
            result["mismatches"].append({
                "type": "guard_without_provider",
                "message": "Auth guard found but no AuthProvider/AuthContext detected in project",
            })

        if has_conditional and not has_guard:
            result["mismatches"].append({
                "type": "conditional_render_without_guard",
                "message": "Conditional screen rendering found but no navigation-level auth guard",
            })

        if not has_guard and not has_conditional:
            result["warnings"].append("No auth guards or conditional renders detected in this file")

        return {"status": "ok", **result}
