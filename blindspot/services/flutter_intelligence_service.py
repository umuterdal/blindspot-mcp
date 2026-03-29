"""
Flutter Intelligence Service - Flutter/Dart-specific code analysis.

Provides cross-file widget tree analysis, route parsing, provider/BLoC mapping,
model schema extraction, dependency injection graph, asset mapping, and
convention detection for Flutter projects.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class FlutterIntelligenceService(BaseService):
    """Flutter/Dart-specific code intelligence for Dart projects."""

    # ── Dart class hierarchy stubs ─────────────────────────────────────
    FLUTTER_CLASS_HIERARCHY = {
        "StatelessWidget": {"extends": "Widget", "package": "flutter/widgets.dart"},
        "StatefulWidget": {"extends": "Widget", "package": "flutter/widgets.dart"},
        "HookWidget": {"extends": "StatelessWidget", "package": "flutter_hooks"},
        "ConsumerWidget": {"extends": "StatelessWidget", "package": "flutter_riverpod"},
        "ConsumerStatefulWidget": {"extends": "StatefulWidget", "package": "flutter_riverpod"},
        "HookConsumerWidget": {"extends": "HookWidget", "package": "hooks_riverpod"},
        "Cubit": {"extends": "BlocBase", "package": "flutter_bloc"},
        "Bloc": {"extends": "BlocBase", "package": "flutter_bloc"},
        "StateNotifier": {"extends": None, "package": "state_notifier"},
        "ChangeNotifier": {"extends": None, "package": "flutter/foundation.dart"},
        "GetxController": {"extends": "DisposableInterface", "package": "get"},
    }

    SCAN_DIRS = {
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

    # ── Pattern registries ─────────────────────────────────────────────
    SIMILAR_PATTERN_REGISTRY = {
        "form": {
            "keywords": ["form", "input", "textfield", "validation", "formkey", "textformfield"],
            "file_patterns": [r"Form\(", r"TextFormField\(", r"GlobalKey<FormState>", r"_formKey"],
            "description": "Form with validation pattern",
        },
        "list-view": {
            "keywords": ["list", "listview", "scroll", "infinite", "pagination"],
            "file_patterns": [r"ListView\.builder\(", r"ListView\.separated\(", r"SliverList\(", r"CustomScrollView\("],
            "description": "Scrollable list view pattern",
        },
        "bottom-sheet": {
            "keywords": ["bottom", "sheet", "modal", "bottomsheet"],
            "file_patterns": [r"showModalBottomSheet\(", r"BottomSheet\(", r"DraggableScrollableSheet\("],
            "description": "Bottom sheet / modal sheet pattern",
        },
        "dialog": {
            "keywords": ["dialog", "alert", "confirm", "popup"],
            "file_patterns": [r"showDialog\(", r"AlertDialog\(", r"SimpleDialog\(", r"CupertinoAlertDialog\("],
            "description": "Dialog / alert pattern",
        },
        "drawer": {
            "keywords": ["drawer", "sidebar", "navigation", "menu"],
            "file_patterns": [r"Drawer\(", r"NavigationDrawer\(", r"EndDrawer"],
            "description": "Navigation drawer pattern",
        },
        "snackbar": {
            "keywords": ["snackbar", "toast", "notification", "message"],
            "file_patterns": [r"ScaffoldMessenger\.of\(", r"SnackBar\(", r"showSnackBar\("],
            "description": "Snackbar / toast notification pattern",
        },
        "animation": {
            "keywords": ["animation", "animate", "transition", "tween", "controller"],
            "file_patterns": [r"AnimationController\(", r"Tween\(", r"AnimatedBuilder\(", r"AnimatedWidget",
                              r"AnimatedContainer\(", r"Hero\(", r"SlideTransition\("],
            "description": "Animation / transition pattern",
        },
        "custom-painter": {
            "keywords": ["paint", "canvas", "draw", "custom", "painter"],
            "file_patterns": [r"CustomPainter\b", r"CustomPaint\(", r"Canvas\b", r"Paint\(\)"],
            "description": "Custom painting / canvas pattern",
        },
        "platform-channel": {
            "keywords": ["platform", "channel", "native", "method", "event"],
            "file_patterns": [r"MethodChannel\(", r"EventChannel\(", r"BasicMessageChannel\(", r"platform"],
            "description": "Platform channel / native bridge pattern",
        },
    }

    CONVENTION_PATTERNS = {
        "naming": {
            "description": "File and class naming conventions",
            "checks": ["file_naming", "class_naming", "widget_naming", "test_naming"],
        },
        "state": {
            "description": "State management pattern conventions",
            "checks": ["riverpod", "bloc", "provider", "getx", "mobx"],
        },
        "architecture": {
            "description": "Architecture pattern conventions",
            "checks": ["clean_arch", "mvvm", "mvc", "feature_first", "layer_first"],
        },
        "testing": {
            "description": "Testing conventions",
            "checks": ["widget_tests", "unit_tests", "integration_tests", "golden_tests"],
        },
        "localization": {
            "description": "Localization / i18n conventions",
            "checks": ["arb_files", "intl", "easy_localization", "hardcoded_strings"],
        },
    }

    # ── Internal helpers ───────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    def _collect_dart_files(self, base: str, subdirs: Optional[List[str]] = None) -> List[Tuple[str, str]]:
        """Collect all .dart files under given subdirectories.

        Returns list of (absolute_path, relative_path) tuples.
        """
        results: List[Tuple[str, str]] = []
        dirs_to_scan = subdirs or ["lib", "test"]
        for subdir in dirs_to_scan:
            full = os.path.join(base, subdir)
            if not os.path.isdir(full):
                continue
            for root, _, files in os.walk(full):
                for fname in files:
                    if fname.endswith(".dart"):
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, base)
                        results.append((fpath, rel))
        return results

    def _read_file(self, fpath: str) -> Optional[str]:
        """Read file content safely."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def _parse_imports(self, content: str) -> List[Dict[str, str]]:
        """Parse Dart import statements."""
        imports: List[Dict[str, str]] = []
        for m in re.finditer(
            r"""import\s+['"]([^'"]+)['"]\s*(?:as\s+(\w+))?\s*(?:show\s+([\w,\s]+))?\s*(?:hide\s+([\w,\s]+))?\s*;""",
            content,
        ):
            imp: Dict[str, str] = {"uri": m.group(1)}
            if m.group(2):
                imp["alias"] = m.group(2)
            if m.group(3):
                imp["show"] = [s.strip() for s in m.group(3).split(",")]
            if m.group(4):
                imp["hide"] = [s.strip() for s in m.group(4).split(",")]
            imports.append(imp)
        return imports

    def _extract_class_info(self, content: str) -> List[Dict[str, Any]]:
        """Extract all class declarations from Dart content."""
        classes: List[Dict[str, Any]] = []
        # Match: [abstract] class Name [extends Parent] [with Mixin1, Mixin2] [implements I1, I2] {
        pattern = re.compile(
            r"(?:abstract\s+)?class\s+(\w+)"
            r"(?:\s+extends\s+([\w.<>]+))?"
            r"(?:\s+with\s+([\w,\s.<>]+))?"
            r"(?:\s+implements\s+([\w,\s.<>]+))?"
            r"\s*\{",
            re.MULTILINE,
        )
        for m in pattern.finditer(content):
            cls: Dict[str, Any] = {
                "name": m.group(1),
                "extends": m.group(2).strip() if m.group(2) else None,
                "mixins": [x.strip() for x in m.group(3).split(",")] if m.group(3) else [],
                "implements": [x.strip() for x in m.group(4).split(",")] if m.group(4) else [],
                "is_abstract": content[max(0, m.start() - 10) : m.start()].strip().endswith("abstract"),
                "offset": m.start(),
            }
            classes.append(cls)
        return classes

    def _extract_class_body(self, content: str, class_offset: int) -> str:
        """Extract the body of a class starting from its opening brace."""
        brace_start = content.find("{", class_offset)
        if brace_start == -1:
            return ""
        depth = 0
        i = brace_start
        while i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    return content[brace_start + 1 : i]
            i += 1
        return content[brace_start + 1 :]

    def _extract_methods(self, class_body: str) -> List[Dict[str, Any]]:
        """Extract methods from a class body."""
        methods: List[Dict[str, Any]] = []
        # Pattern: [return_type] methodName([params]) [async] {
        pat = re.compile(
            r"(?:@override\s+)?(?:static\s+)?(?:Future<[\w<>?,\s]+>|Stream<[\w<>?,\s]+>|"
            r"[\w<>?]+)\s+(\w+)\s*\(([^)]*)\)\s*(?:async\s*\*?\s*)?\{",
            re.MULTILINE,
        )
        for m in pat.finditer(class_body):
            method_name = m.group(1)
            params_str = m.group(2).strip()
            params = []
            if params_str:
                # Simple param parsing - split by commas outside angle brackets
                depth = 0
                current = ""
                for ch in params_str:
                    if ch in "<({":
                        depth += 1
                    elif ch in ">)}":
                        depth -= 1
                    if ch == "," and depth == 0:
                        params.append(current.strip())
                        current = ""
                    else:
                        current += ch
                if current.strip():
                    params.append(current.strip())
            methods.append({
                "name": method_name,
                "params": params,
                "is_async": "async" in class_body[m.start() : m.end()],
            })
        return methods

    def _extract_fields(self, class_body: str) -> List[Dict[str, Any]]:
        """Extract field declarations from a class body."""
        fields: List[Dict[str, Any]] = []
        # Pattern: [final] Type[?] fieldName; or [final] Type[?] fieldName = value;
        pat = re.compile(
            r"^\s*(final\s+|late\s+final\s+|late\s+|static\s+final\s+|static\s+)?"
            r"([\w<>?,\s]+?)\s+(\w+)\s*(?:=\s*([^;]+))?\s*;",
            re.MULTILINE,
        )
        for m in pat.finditer(class_body):
            modifier = (m.group(1) or "").strip()
            type_str = m.group(2).strip()
            name = m.group(3)
            default = m.group(4).strip() if m.group(4) else None
            # Skip method-like patterns
            if name in ("return", "if", "else", "for", "while", "switch", "try", "catch"):
                continue
            fields.append({
                "name": name,
                "type": type_str,
                "is_final": "final" in modifier,
                "is_late": "late" in modifier,
                "is_static": "static" in modifier,
                "is_nullable": type_str.endswith("?"),
                "default_value": default,
            })
        return fields

    def _find_widget_children(self, class_body: str) -> List[str]:
        """Find child widget references in a widget's build method."""
        children: List[str] = []
        # Find Widget-typed constructor params
        param_pat = re.compile(r"(?:required\s+)?(?:Widget|Key)\??\s+(\w+)")
        for m in param_pat.finditer(class_body):
            children.append(m.group(1))

        # Find widget instantiations in build method
        build_match = re.search(r"Widget\s+build\s*\(", class_body)
        if build_match:
            build_body = class_body[build_match.start() :]
            # Find PascalCase constructors (widget instantiations)
            widget_pat = re.compile(r"\b([A-Z]\w+)\s*\(")
            seen: Set[str] = set()
            for wm in widget_pat.finditer(build_body):
                name = wm.group(1)
                # Exclude common non-widget classes
                if name not in seen and name not in (
                    "BuildContext", "Key", "Widget", "State", "Color", "EdgeInsets",
                    "TextStyle", "BoxDecoration", "BorderRadius", "Size", "Offset",
                    "Duration", "Alignment", "Border", "Icon", "Icons", "Colors",
                    "FontWeight", "TextAlign", "MainAxisAlignment", "CrossAxisAlignment",
                    "MainAxisSize", "Axis", "BoxConstraints", "MediaQuery", "Theme",
                    "Navigator", "MaterialPageRoute", "Future", "Stream", "Map", "List",
                    "Set", "String", "int", "double", "bool", "Null", "Object",
                    "Exception", "Error", "Type", "Function", "Iterable",
                ):
                    children.append(name)
                    seen.add(name)

        return children

    def _detect_widget_type(self, extends: Optional[str]) -> str:
        """Classify widget type from its extends clause."""
        if not extends:
            return "unknown"
        type_map = {
            "StatelessWidget": "stateless",
            "StatefulWidget": "stateful",
            "HookWidget": "hook",
            "HookConsumerWidget": "hook_consumer",
            "ConsumerWidget": "consumer",
            "ConsumerStatefulWidget": "consumer_stateful",
        }
        for key, value in type_map.items():
            if key in extends:
                return value
        return "other"

    # ── 1. get_widget_tree ─────────────────────────────────────────────

    def get_widget_tree(self, widget_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Dart files for widget classes and build widget hierarchy.

        Extracts: build() method, child widgets, State class, initState/dispose,
        widget type (Stateless/Stateful/Hook/Consumer).

        Args:
            widget_name: Optional specific widget name. If omitted, returns all widgets.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {"widgets": {}, "hierarchy": {}, "total": 0}
        dart_files = self._collect_dart_files(base, ["lib"])

        # First pass: collect all widget classes
        widget_classes: Dict[str, Dict[str, Any]] = {}
        state_classes: Dict[str, Dict[str, Any]] = {}

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            classes = self._extract_class_info(content)
            for cls in classes:
                name = cls["name"]
                extends = cls.get("extends", "")

                # Filter if specific widget requested
                if widget_name and name != widget_name and not name.endswith(f"_{widget_name}State"):
                    continue

                is_widget = any(
                    w in (extends or "")
                    for w in ("StatelessWidget", "StatefulWidget", "HookWidget",
                              "ConsumerWidget", "ConsumerStatefulWidget", "HookConsumerWidget")
                )
                is_state = extends and "State<" in extends

                body = self._extract_class_body(content, cls["offset"])

                if is_widget:
                    widget_type = self._detect_widget_type(extends)
                    methods = self._extract_methods(body)
                    children = self._find_widget_children(body)
                    fields = self._extract_fields(body)

                    # Extract build method presence and complexity
                    build_match = re.search(r"Widget\s+build\s*\(", body)
                    has_build = build_match is not None

                    # Check for key constructor parameters (props)
                    constructor_pat = re.compile(
                        rf"(?:const\s+)?{re.escape(name)}\s*\(\s*\{{([^}}]*)\}}\s*\)",
                        re.DOTALL,
                    )
                    constructor_match = constructor_pat.search(body)
                    props: List[Dict[str, Any]] = []
                    if constructor_match:
                        params_str = constructor_match.group(1)
                        # Parse named parameters
                        param_items = re.findall(
                            r"(?:required\s+)?([\w<>?,\s]+?)\s+(\w+)\s*(?:=\s*([^,}]+))?",
                            params_str,
                        )
                        for ptype, pname, pdefault in param_items:
                            if pname in ("key", "super"):
                                continue
                            props.append({
                                "name": pname,
                                "type": ptype.strip(),
                                "required": "required" in params_str.split(pname)[0].split(",")[-1],
                                "default": pdefault.strip() if pdefault else None,
                            })

                    widget_classes[name] = {
                        "name": name,
                        "type": widget_type,
                        "extends": extends,
                        "mixins": cls.get("mixins", []),
                        "file": rel_path,
                        "has_build_method": has_build,
                        "props": props[:20],
                        "children": children[:30],
                        "methods": [m["name"] for m in methods][:20],
                        "fields": [{"name": f["name"], "type": f["type"]} for f in fields][:15],
                    }

                elif is_state:
                    # Extract State class details
                    state_widget_match = re.search(r"State<(\w+)>", extends)
                    widget_for = state_widget_match.group(1) if state_widget_match else None

                    methods = self._extract_methods(body)
                    method_names = [m["name"] for m in methods]

                    has_init_state = "initState" in method_names
                    has_dispose = "dispose" in method_names
                    has_did_change = "didChangeDependencies" in method_names
                    has_did_update = "didUpdateWidget" in method_names

                    # Extract setState calls
                    set_state_calls = len(re.findall(r"setState\s*\(", body))

                    state_classes[name] = {
                        "name": name,
                        "widget_for": widget_for,
                        "file": rel_path,
                        "has_init_state": has_init_state,
                        "has_dispose": has_dispose,
                        "has_did_change_dependencies": has_did_change,
                        "has_did_update_widget": has_did_update,
                        "set_state_count": set_state_calls,
                        "methods": method_names[:20],
                    }

        # Second pass: link state classes to widgets
        for state_name, state_info in state_classes.items():
            widget_for = state_info.get("widget_for")
            if widget_for and widget_for in widget_classes:
                widget_classes[widget_for]["state_class"] = state_info

        # Build hierarchy - determine parent->child relationships
        hierarchy: Dict[str, List[str]] = {}
        for wname, winfo in widget_classes.items():
            children_in_project = [
                c for c in winfo.get("children", []) if c in widget_classes
            ]
            if children_in_project:
                hierarchy[wname] = children_in_project

        results["widgets"] = widget_classes
        results["hierarchy"] = hierarchy
        results["total"] = len(widget_classes)
        results["stateful_count"] = sum(1 for w in widget_classes.values() if w["type"] == "stateful")
        results["stateless_count"] = sum(1 for w in widget_classes.values() if w["type"] == "stateless")
        results["consumer_count"] = sum(1 for w in widget_classes.values() if "consumer" in w["type"])

        return results

    # ── 2. get_route_map ───────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse GoRouter, Navigator, and auto_route route definitions.

        Extracts: path, builder/page widget, redirect guards, nested routes, route names.

        Args:
            filter_prefix: Optional route path prefix filter (e.g., "/auth").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "routes": [],
            "router_type": "unknown",
            "total": 0,
            "named_routes": {},
            "guards": [],
        }

        dart_files = self._collect_dart_files(base, ["lib"])

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── GoRouter detection ──────────────────────────────────
            if "GoRouter" in content or "GoRoute" in content:
                results["router_type"] = "go_router"

                # Parse GoRoute definitions
                go_route_pat = re.compile(
                    r"GoRoute\s*\(\s*((?:[^()]*|\((?:[^()]*|\([^()]*\))*\))*)\s*\)",
                    re.DOTALL,
                )
                for rm in go_route_pat.finditer(content):
                    route_body = rm.group(1)

                    # Extract path
                    path_match = re.search(r"""path:\s*['"]([^'"]+)['"]""", route_body)
                    path = path_match.group(1) if path_match else "unknown"

                    if filter_prefix and not path.startswith(filter_prefix):
                        continue

                    # Extract name
                    name_match = re.search(r"""name:\s*['"]([^'"]+)['"]""", route_body)
                    name = name_match.group(1) if name_match else None
                    # Also handle constant references: name: RouteName.home
                    if not name:
                        name_ref = re.search(r"name:\s*(\w+\.\w+)", route_body)
                        name = name_ref.group(1) if name_ref else None

                    # Extract builder / pageBuilder
                    builder_match = re.search(
                        r"(?:builder|pageBuilder)\s*:\s*\([^)]*\)\s*(?:=>|{)\s*(?:return\s+)?(\w+)",
                        route_body,
                    )
                    builder_widget = builder_match.group(1) if builder_match else None

                    # Extract redirect
                    redirect_match = re.search(r"redirect:\s*\(", route_body)
                    has_redirect = redirect_match is not None

                    # Check for nested routes
                    has_routes = "routes:" in route_body

                    route_info: Dict[str, Any] = {
                        "path": path,
                        "file": rel_path,
                        "type": "go_route",
                    }
                    if name:
                        route_info["name"] = name
                        results["named_routes"][name] = path
                    if builder_widget:
                        route_info["widget"] = builder_widget
                    if has_redirect:
                        route_info["has_redirect"] = True
                    if has_routes:
                        route_info["has_nested_routes"] = True

                    results["routes"].append(route_info)

                # Parse ShellRoute
                shell_pat = re.compile(r"ShellRoute\s*\(", re.DOTALL)
                for _ in shell_pat.finditer(content):
                    results["routes"].append({
                        "path": "(shell)",
                        "file": rel_path,
                        "type": "shell_route",
                    })

                # Parse redirect guards
                redirect_pat = re.compile(
                    r"redirect:\s*\([^)]*\)\s*(?:=>|\{)(.*?)(?:\}|,\s*(?:routes|path|name|builder))",
                    re.DOTALL,
                )
                for rg in redirect_pat.finditer(content):
                    guard_body = rg.group(1)
                    conditions = re.findall(r"if\s*\(([^)]+)\)", guard_body)
                    redirect_targets = re.findall(r"""['"](/[^'"]+)['"]""", guard_body)
                    if conditions or redirect_targets:
                        results["guards"].append({
                            "file": rel_path,
                            "conditions": conditions[:5],
                            "redirect_targets": redirect_targets[:5],
                        })

            # ── Navigator route detection ───────────────────────────
            if "MaterialPageRoute" in content or "CupertinoPageRoute" in content or "Route<" in content:
                if results["router_type"] == "unknown":
                    results["router_type"] = "navigator"

                # Named routes map
                named_routes_pat = re.compile(
                    r"""['"]([/\w:]+)['"]\s*:\s*\([^)]*\)\s*(?:=>|{)\s*(?:return\s+)?(?:const\s+)?(\w+)""",
                )
                for nr in named_routes_pat.finditer(content):
                    path = nr.group(1)
                    widget = nr.group(2)
                    if filter_prefix and not path.startswith(filter_prefix):
                        continue
                    results["routes"].append({
                        "path": path,
                        "widget": widget,
                        "file": rel_path,
                        "type": "named_route",
                    })
                    results["named_routes"][path] = widget

                # Navigator.push / Navigator.pushNamed calls
                push_named_pat = re.compile(
                    r"""Navigator\.\w*push\w*\s*\([^,]*,?\s*['"]([^'"]+)['"]"""
                )
                for pn in push_named_pat.finditer(content):
                    route = pn.group(1)
                    if filter_prefix and not route.startswith(filter_prefix):
                        continue
                    # Track navigation usage
                    results["routes"].append({
                        "path": route,
                        "file": rel_path,
                        "type": "push_named",
                    })

            # ── auto_route detection ────────────────────────────────
            if "@RoutePage" in content or "AutoRoute" in content or "@AutoRouterConfig" in content:
                if results["router_type"] == "unknown":
                    results["router_type"] = "auto_route"

                # Parse AutoRoute definitions
                auto_route_pat = re.compile(
                    r"""AutoRoute\s*\(\s*(?:.*?path:\s*['"]([^'"]+)['"])?(?:.*?page:\s*(\w+))?""",
                    re.DOTALL,
                )
                for ar in auto_route_pat.finditer(content):
                    path = ar.group(1) or "auto"
                    page = ar.group(2)
                    if filter_prefix and path != "auto" and not path.startswith(filter_prefix):
                        continue
                    route_info = {
                        "path": path,
                        "file": rel_path,
                        "type": "auto_route",
                    }
                    if page:
                        route_info["page"] = page
                    results["routes"].append(route_info)

                # Parse guards
                guard_pat = re.compile(r"class\s+(\w+)\s+extends\s+AutoRouteGuard")
                for gm in guard_pat.finditer(content):
                    results["guards"].append({
                        "name": gm.group(1),
                        "file": rel_path,
                        "type": "auto_route_guard",
                    })

        results["total"] = len(results["routes"])
        return results

    # ── 3. get_model_schema ────────────────────────────────────────────

    def get_model_schema(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Dart model classes for serialization and data structure.

        Extracts: fromJson, toJson, copyWith, freezed/json_serializable annotations,
        fields, types, nullable, default values.

        Args:
            model_name: Optional specific model name. If omitted, returns all models.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {"models": {}, "total": 0, "serialization_type": "unknown"}
        model_dirs = ["lib/models", "lib/data/models", "lib/domain/entities", "lib/entities",
                      "lib/features"]
        dart_files = self._collect_dart_files(base, model_dirs + ["lib"])

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            classes = self._extract_class_info(content)
            for cls in classes:
                name = cls["name"]
                if model_name and name != model_name:
                    continue

                body = self._extract_class_body(content, cls["offset"])
                fields = self._extract_fields(body)

                # Check if this looks like a model (has serialization or fields)
                has_from_json = bool(re.search(r"factory\s+" + re.escape(name) + r"\.fromJson", body))
                has_to_json = bool(re.search(r"(?:Map<String,\s*dynamic>|Map)\s+toJson\s*\(", body))
                has_copy_with = bool(re.search(r"" + re.escape(name) + r"\s+copyWith\s*\(", body))
                has_freezed = bool(re.search(r"@freezed|@Freezed", content[:cls["offset"] + 200]))
                has_json_serializable = bool(
                    re.search(r"@JsonSerializable", content[:cls["offset"] + 200])
                )
                has_json_key = "@JsonKey" in body
                has_hive = bool(re.search(r"@HiveType|@HiveField", content[:cls["offset"] + 200]))

                # Skip non-model classes (no fields, no serialization)
                is_model = (
                    has_from_json or has_to_json or has_freezed or
                    has_json_serializable or has_hive or
                    (len(fields) >= 2 and not cls.get("extends", "").endswith("Widget"))
                )
                if not is_model and model_name is None:
                    continue

                # Determine serialization type
                if has_freezed:
                    ser_type = "freezed"
                    results["serialization_type"] = "freezed"
                elif has_json_serializable:
                    ser_type = "json_serializable"
                    if results["serialization_type"] == "unknown":
                        results["serialization_type"] = "json_serializable"
                elif has_from_json and has_to_json:
                    ser_type = "manual"
                    if results["serialization_type"] == "unknown":
                        results["serialization_type"] = "manual"
                elif has_hive:
                    ser_type = "hive"
                else:
                    ser_type = "none"

                # Parse fromJson factory for field mappings
                json_field_mappings: Dict[str, str] = {}
                from_json_body_match = re.search(
                    r"factory\s+" + re.escape(name) + r"\.fromJson\s*\([^)]*\)\s*(?:=>|{)(.*?)(?:;|\})",
                    body, re.DOTALL,
                )
                if from_json_body_match:
                    fjb = from_json_body_match.group(1)
                    # Extract json['key'] or json['key'] as Type mappings
                    for jm in re.finditer(r"""json\[['"](\w+)['"]\]""", fjb):
                        json_field_mappings[jm.group(1)] = jm.group(1)

                # Parse @JsonKey annotations for field renaming
                json_keys: Dict[str, Dict[str, Any]] = {}
                for jk in re.finditer(
                    r"@JsonKey\(([^)]+)\)\s*(?:final\s+)?([\w<>?,\s]+?)\s+(\w+)",
                    body, re.DOTALL,
                ):
                    jk_params = jk.group(1)
                    field_name = jk.group(3)
                    jk_info: Dict[str, Any] = {}
                    name_match = re.search(r"""name:\s*['"]([^'"]+)['"]""", jk_params)
                    if name_match:
                        jk_info["json_name"] = name_match.group(1)
                    default_match = re.search(r"defaultValue:\s*([^,)]+)", jk_params)
                    if default_match:
                        jk_info["default_value"] = default_match.group(1).strip()
                    if "ignore: true" in jk_params or "includeFromJson: false" in jk_params:
                        jk_info["ignored"] = True
                    json_keys[field_name] = jk_info

                # Parse freezed union types
                union_cases: List[str] = []
                if has_freezed:
                    for uc in re.finditer(
                        r"(?:const\s+)?factory\s+" + re.escape(name) + r"\.(\w+)\s*\(",
                        body,
                    ):
                        union_cases.append(uc.group(1))

                model_info: Dict[str, Any] = {
                    "name": name,
                    "file": rel_path,
                    "extends": cls.get("extends"),
                    "implements": cls.get("implements", []),
                    "serialization": ser_type,
                    "has_from_json": has_from_json,
                    "has_to_json": has_to_json,
                    "has_copy_with": has_copy_with,
                    "fields": [
                        {
                            "name": f["name"],
                            "type": f["type"],
                            "nullable": f["is_nullable"],
                            "final": f["is_final"],
                            "default": f.get("default_value"),
                            **({"json_key": json_keys[f["name"]]} if f["name"] in json_keys else {}),
                        }
                        for f in fields
                    ][:50],
                }
                if json_field_mappings:
                    model_info["json_mappings"] = json_field_mappings
                if union_cases:
                    model_info["union_cases"] = union_cases
                if has_hive:
                    model_info["hive_type"] = True

                results["models"][name] = model_info

        results["total"] = len(results["models"])
        return results

    # ── 4. get_provider_map ────────────────────────────────────────────

    def get_provider_map(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Riverpod, BLoC, and Provider package state management.

        Maps provider definitions to their consumer widgets.

        Args:
            provider_name: Optional specific provider/bloc name.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "state_management": "unknown",
            "providers": {},
            "blocs": {},
            "consumers": {},
            "total": 0,
        }

        dart_files = self._collect_dart_files(base, ["lib"])

        # ── First pass: discover providers and blocs ────────────────
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── Riverpod providers ──────────────────────────────────
            # final myProvider = Provider<Type>((ref) => ...)
            # final myProvider = StateNotifierProvider<Notifier, State>((ref) => ...)
            # final myProvider = FutureProvider<Type>((ref) => ...)
            # final myProvider = StreamProvider<Type>((ref) => ...)
            # final myProvider = NotifierProvider<Notifier, State>(() => ...)
            # @riverpod annotation
            riverpod_pat = re.compile(
                r"(?:final|const)\s+(\w+)\s*=\s*(Provider|StateNotifierProvider|"
                r"FutureProvider|StreamProvider|NotifierProvider|AsyncNotifierProvider|"
                r"StateProvider|ChangeNotifierProvider|AutoDisposeProvider|"
                r"AutoDisposeFutureProvider|AutoDisposeStreamProvider|"
                r"AutoDisposeStateNotifierProvider|AutoDisposeNotifierProvider)"
                r"(?:<([^>]+)>)?\s*\(",
            )
            for pm in riverpod_pat.finditer(content):
                pname = pm.group(1)
                ptype = pm.group(2)
                pgeneric = pm.group(3)
                if provider_name and pname != provider_name:
                    continue
                results["state_management"] = "riverpod"
                results["providers"][pname] = {
                    "name": pname,
                    "type": ptype,
                    "generic": pgeneric,
                    "file": rel_path,
                    "consumers": [],
                }

            # Riverpod code-gen (@riverpod annotation)
            riverpod_gen_pat = re.compile(
                r"@(?:riverpod|Riverpod)\s*(?:\([^)]*\))?\s*"
                r"(?:class\s+(\w+)|(?:[\w<>?,\s]+)\s+(\w+)\s*\()",
            )
            for rg in riverpod_gen_pat.finditer(content):
                pname = rg.group(1) or rg.group(2)
                if not pname:
                    continue
                if provider_name and pname != provider_name:
                    continue
                # Code-gen provider name is camelCase + Provider
                gen_provider_name = pname[0].lower() + pname[1:] + "Provider" if rg.group(1) else pname + "Provider"
                results["state_management"] = "riverpod"
                results["providers"][gen_provider_name] = {
                    "name": gen_provider_name,
                    "type": "riverpod_codegen",
                    "class_name": pname,
                    "file": rel_path,
                    "consumers": [],
                }

            # ── BLoC / Cubit ────────────────────────────────────────
            classes = self._extract_class_info(content)
            for cls in classes:
                extends = cls.get("extends", "") or ""
                cname = cls["name"]

                if "Bloc<" in extends or "Cubit<" in extends:
                    if provider_name and cname != provider_name:
                        continue
                    body = self._extract_class_body(content, cls["offset"])

                    # Extract state type
                    state_type_match = re.search(r"(?:Bloc|Cubit)<(\w+)>", extends)
                    state_type = state_type_match.group(1) if state_type_match else None

                    # Extract events (for Bloc)
                    events: List[str] = []
                    if "Bloc<" in extends:
                        event_pat = re.compile(r"on<(\w+)>\s*\(")
                        for em in event_pat.finditer(body):
                            events.append(em.group(1))

                    results["state_management"] = "bloc"
                    results["blocs"][cname] = {
                        "name": cname,
                        "type": "bloc" if "Bloc<" in extends else "cubit",
                        "state_type": state_type,
                        "events": events[:20],
                        "file": rel_path,
                        "consumers": [],
                    }

                # ── Provider package ChangeNotifier ─────────────────
                if "ChangeNotifier" in extends and "ChangeNotifierProvider" not in extends:
                    if provider_name and cname != provider_name:
                        continue
                    body = self._extract_class_body(content, cls["offset"])
                    notify_count = len(re.findall(r"notifyListeners\s*\(", body))

                    if results["state_management"] == "unknown":
                        results["state_management"] = "provider"
                    results["providers"][cname] = {
                        "name": cname,
                        "type": "ChangeNotifier",
                        "notify_listeners_count": notify_count,
                        "file": rel_path,
                        "consumers": [],
                    }

        # ── Second pass: find consumers ─────────────────────────────
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Riverpod consumers: ref.watch(provider), ref.read(provider)
            riverpod_consumer_pat = re.compile(r"ref\.(watch|read|listen)\s*\(\s*(\w+)")
            for rc in riverpod_consumer_pat.finditer(content):
                usage_type = rc.group(1)
                pname = rc.group(2)
                if pname in results["providers"]:
                    results["providers"][pname]["consumers"].append({
                        "file": rel_path,
                        "usage": usage_type,
                    })

            # BLoC consumers: BlocBuilder<Bloc, State>, BlocListener, BlocConsumer
            # context.read<Bloc>(), context.watch<Bloc>()
            bloc_consumer_pat = re.compile(
                r"(?:BlocBuilder|BlocListener|BlocConsumer|BlocSelector)<(\w+)"
            )
            for bc in bloc_consumer_pat.finditer(content):
                bloc_name = bc.group(1)
                if bloc_name in results["blocs"]:
                    results["blocs"][bloc_name]["consumers"].append({
                        "file": rel_path,
                        "usage": "widget",
                    })

            context_bloc_pat = re.compile(r"context\.(read|watch|select)<(\w+)>\s*\(")
            for cb in context_bloc_pat.finditer(content):
                usage_type = cb.group(1)
                bloc_name = cb.group(2)
                if bloc_name in results["blocs"]:
                    results["blocs"][bloc_name]["consumers"].append({
                        "file": rel_path,
                        "usage": f"context.{usage_type}",
                    })

            # Provider package consumers: Provider.of<T>(context), context.watch<T>()
            provider_of_pat = re.compile(r"Provider\.of<(\w+)>\s*\(")
            for po in provider_of_pat.finditer(content):
                pname = po.group(1)
                if pname in results["providers"]:
                    results["providers"][pname]["consumers"].append({
                        "file": rel_path,
                        "usage": "Provider.of",
                    })

        # Deduplicate consumers
        for pname in results["providers"]:
            seen: Set[str] = set()
            unique: List[Dict[str, str]] = []
            for c in results["providers"][pname]["consumers"]:
                key = f"{c['file']}:{c['usage']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(c)
            results["providers"][pname]["consumers"] = unique[:20]

        for bname in results["blocs"]:
            seen = set()
            unique = []
            for c in results["blocs"][bname]["consumers"]:
                key = f"{c['file']}:{c['usage']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(c)
            results["blocs"][bname]["consumers"] = unique[:20]

        results["total"] = len(results["providers"]) + len(results["blocs"])
        return results

    # ── 5. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace a complete flow: Route -> guard -> screen widget -> BLoC/provider
        -> repository -> API service -> model.

        Args:
            entry_point: Widget name, route path, or file path.
            method: Optional method to trace specifically.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        flow: Dict[str, Any] = {
            "entry_point": entry_point,
            "steps": [],
            "warnings": [],
        }

        dart_files = self._collect_dart_files(base, ["lib"])
        file_contents: Dict[str, str] = {}
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if content:
                file_contents[rel_path] = content

        # Step 1: Find entry point (route or widget)
        target_widget: Optional[str] = None
        route_info: Optional[Dict[str, Any]] = None

        # Check if entry_point is a route path
        if entry_point.startswith("/"):
            for rel_path, content in file_contents.items():
                path_match = re.search(
                    rf"""path:\s*['"]""" + re.escape(entry_point) + r"""['"]""", content
                )
                if path_match:
                    # Find associated builder widget
                    builder_match = re.search(
                        r"(?:builder|pageBuilder)\s*:\s*\([^)]*\)\s*(?:=>|{)\s*(?:return\s+)?(?:const\s+)?(\w+)",
                        content[path_match.start() :],
                    )
                    if builder_match:
                        target_widget = builder_match.group(1)
                    route_info = {"path": entry_point, "file": rel_path}
                    flow["steps"].append({
                        "type": "route",
                        "path": entry_point,
                        "file": rel_path,
                        "widget": target_widget,
                    })
                    break
        else:
            # entry_point is a widget name
            target_widget = entry_point

        if not target_widget:
            flow["warnings"].append(f"Could not resolve entry point: {entry_point}")
            return flow

        # Step 2: Find the widget file and its dependencies
        widget_file: Optional[str] = None
        widget_content: Optional[str] = None
        for rel_path, content in file_contents.items():
            if re.search(rf"class\s+{re.escape(target_widget)}\s+extends\s+\w+", content):
                widget_file = rel_path
                widget_content = content
                flow["steps"].append({
                    "type": "screen_widget",
                    "name": target_widget,
                    "file": rel_path,
                })
                break

        if not widget_content:
            flow["warnings"].append(f"Widget {target_widget} not found in project")
            return flow

        # Step 3: Find state management usage in the widget
        # Riverpod: ref.watch, ref.read
        riverpod_usages = re.findall(r"ref\.(watch|read|listen)\s*\(\s*(\w+)", widget_content)
        for usage_type, pname in riverpod_usages:
            flow["steps"].append({
                "type": "provider",
                "name": pname,
                "usage": usage_type,
            })
            # Trace provider -> repository/service
            self._trace_provider_dependencies(pname, file_contents, flow, method)

        # BLoC: context.read<Bloc>(), BlocBuilder
        bloc_usages = re.findall(r"context\.(read|watch)<(\w+)>", widget_content)
        bloc_widget_usages = re.findall(r"(?:BlocBuilder|BlocConsumer|BlocListener)<(\w+)", widget_content)
        all_bloc_names = set(b[1] for b in bloc_usages) | set(bloc_widget_usages)
        for bname in all_bloc_names:
            flow["steps"].append({
                "type": "bloc",
                "name": bname,
            })
            self._trace_bloc_dependencies(bname, file_contents, flow, method)

        # Step 4: Find direct service/repository calls
        import_pat = re.compile(r"""import\s+['"]([^'"]+)['"]""")
        for imp in import_pat.finditer(widget_content):
            uri = imp.group(1)
            if "service" in uri.lower() or "repository" in uri.lower() or "repo" in uri.lower():
                flow["steps"].append({
                    "type": "import",
                    "uri": uri,
                    "category": "service/repository",
                })

        return flow

    def _trace_provider_dependencies(
        self, provider_name: str, file_contents: Dict[str, str],
        flow: Dict[str, Any], method: Optional[str],
    ) -> None:
        """Trace dependencies from a Riverpod provider."""
        for rel_path, content in file_contents.items():
            # Find provider definition
            pat = re.compile(
                rf"(?:final|const)\s+{re.escape(provider_name)}\s*=\s*\w+Provider"
            )
            if not pat.search(content):
                continue

            # Find ref.watch/ref.read inside provider body
            inner_deps = re.findall(r"ref\.(watch|read)\s*\(\s*(\w+)", content)
            for usage_type, dep_name in inner_deps:
                if dep_name != provider_name:
                    flow["steps"].append({
                        "type": "provider_dependency",
                        "name": dep_name,
                        "usage": usage_type,
                        "from": provider_name,
                    })

            # Find repository/service class instantiations
            class_usages = re.findall(r"(\w+Repository|\w+Service)\s*\(", content)
            for cls_name in class_usages:
                flow["steps"].append({
                    "type": "repository_or_service",
                    "name": cls_name,
                    "from": provider_name,
                    "file": rel_path,
                })
                self._trace_repository_dependencies(cls_name, file_contents, flow, method)
            break

    def _trace_bloc_dependencies(
        self, bloc_name: str, file_contents: Dict[str, str],
        flow: Dict[str, Any], method: Optional[str],
    ) -> None:
        """Trace dependencies from a BLoC/Cubit."""
        for rel_path, content in file_contents.items():
            if not re.search(rf"class\s+{re.escape(bloc_name)}\s+extends", content):
                continue
            # Find repository/service constructor injections
            constructor_pat = re.compile(
                rf"{re.escape(bloc_name)}\s*\(\s*\{{?([^)]*)\}}?\s*\)"
            )
            cm = constructor_pat.search(content)
            if cm:
                params = cm.group(1)
                repo_params = re.findall(r"(\w+Repository|\w+Service)\s+\w+", params)
                for rp in repo_params:
                    flow["steps"].append({
                        "type": "repository_or_service",
                        "name": rp,
                        "from": bloc_name,
                        "file": rel_path,
                    })
                    self._trace_repository_dependencies(rp, file_contents, flow, method)
            break

    def _trace_repository_dependencies(
        self, class_name: str, file_contents: Dict[str, str],
        flow: Dict[str, Any], method: Optional[str],
    ) -> None:
        """Trace dependencies from a repository/service class."""
        for rel_path, content in file_contents.items():
            if not re.search(rf"class\s+{re.escape(class_name)}\b", content):
                continue

            # Find API calls (http, dio, etc.)
            api_calls = re.findall(
                r"(?:_?(?:dio|http|client|api))\.\s*(get|post|put|delete|patch)\s*\(\s*['\"](.*?)['\"]",
                content, re.IGNORECASE,
            )
            for http_method, url in api_calls:
                flow["steps"].append({
                    "type": "api_call",
                    "method": http_method.upper(),
                    "url": url[:100],
                    "from": class_name,
                    "file": rel_path,
                })

            # Find model usage (fromJson, toJson)
            model_usages = re.findall(r"(\w+)\.fromJson\(", content)
            for mname in model_usages:
                flow["steps"].append({
                    "type": "model",
                    "name": mname,
                    "usage": "fromJson",
                    "from": class_name,
                })
            break

    # ── 6. get_dependency_injection ────────────────────────────────────

    def get_dependency_injection(self) -> Dict[str, Any]:
        """
        Parse dependency injection setup: get_it, injectable, riverpod providers.

        Builds a dependency graph of registered services.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "di_framework": "unknown",
            "registrations": [],
            "dependency_graph": {},
            "total": 0,
        }

        dart_files = self._collect_dart_files(base, ["lib"])

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # ── get_it registrations ────────────────────────────────
            # GetIt.instance.registerSingleton<Type>(Implementation())
            # GetIt.I.registerFactory<Type>(() => Implementation())
            # sl.registerLazySingleton<Type>(() => Implementation())
            getit_pat = re.compile(
                r"(?:GetIt\.(?:instance|I)|(?:sl|getIt|locator|di))\s*\.\s*"
                r"(registerSingleton|registerFactory|registerLazySingleton|"
                r"registerFactoryAsync|registerSingletonAsync|"
                r"registerLazySingletonAsync)"
                r"(?:<(\w+)>)?\s*\(\s*(?:\(\)\s*(?:=>|{)\s*)?(\w+)?",
            )
            for gm in getit_pat.finditer(content):
                reg_type = gm.group(1)
                interface_type = gm.group(2)
                impl_class = gm.group(3)
                results["di_framework"] = "get_it"
                reg_info: Dict[str, Any] = {
                    "registration_type": reg_type,
                    "file": rel_path,
                }
                if interface_type:
                    reg_info["interface"] = interface_type
                if impl_class:
                    reg_info["implementation"] = impl_class
                results["registrations"].append(reg_info)

            # ── injectable annotations ──────────────────────────────
            # @injectable, @singleton, @lazySingleton
            injectable_pat = re.compile(
                r"@(injectable|singleton|lazySingleton|module)\s*(?:\([^)]*\))?\s*"
                r"(?:abstract\s+)?class\s+(\w+)"
            )
            for im in injectable_pat.finditer(content):
                anno_type = im.group(1)
                class_name = im.group(2)
                if results["di_framework"] == "unknown":
                    results["di_framework"] = "injectable"
                results["registrations"].append({
                    "registration_type": anno_type,
                    "class": class_name,
                    "file": rel_path,
                })

            # ── Riverpod providers as DI (already caught above, but for graph) ──
            riverpod_pat = re.compile(
                r"(?:final|const)\s+(\w+)\s*=\s*(\w+Provider)(?:<[^>]+>)?\s*\(",
            )
            for rp in riverpod_pat.finditer(content):
                pname = rp.group(1)
                ptype = rp.group(2)
                if results["di_framework"] == "unknown":
                    results["di_framework"] = "riverpod"
                results["registrations"].append({
                    "registration_type": ptype,
                    "name": pname,
                    "file": rel_path,
                })

        # Build dependency graph from constructor injections
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            classes = self._extract_class_info(content)
            for cls in classes:
                cname = cls["name"]
                body = self._extract_class_body(content, cls["offset"])

                # Find constructor injections
                constructor_pat = re.compile(
                    rf"(?:const\s+)?{re.escape(cname)}\s*\(\s*\{{?([^)]*)\}}?\s*\)",
                    re.DOTALL,
                )
                cm = constructor_pat.search(body)
                if cm:
                    params = cm.group(1)
                    # Find typed parameters that look like service/repo injections
                    dep_pat = re.compile(r"(?:required\s+)?(?:this\.)?(\w+(?:Service|Repository|Client|Api|Bloc|Cubit|Notifier))\s+")
                    deps = dep_pat.findall(params)
                    if deps:
                        results["dependency_graph"][cname] = {
                            "dependencies": deps,
                            "file": rel_path,
                        }

        results["total"] = len(results["registrations"])
        return results

    # ── 7. get_asset_map ───────────────────────────────────────────────

    def get_asset_map(self) -> Dict[str, Any]:
        """
        Parse pubspec.yaml assets and fonts. Find unused assets and missing references.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {
            "declared_assets": [],
            "declared_fonts": [],
            "used_assets": [],
            "unused_assets": [],
            "missing_assets": [],
            "total_declared": 0,
            "total_used": 0,
        }

        # Parse pubspec.yaml
        pubspec_path = os.path.join(base, "pubspec.yaml")
        pubspec_content = self._read_file(pubspec_path)
        if not pubspec_content:
            return {"status": "error", "message": "pubspec.yaml not found"}

        # Extract assets section
        assets_match = re.search(
            r"assets:\s*\n((?:\s+-\s+[^\n]+\n?)+)",
            pubspec_content,
        )
        if assets_match:
            asset_lines = assets_match.group(1)
            for am in re.finditer(r"-\s+(.+)", asset_lines):
                asset_path = am.group(1).strip()
                results["declared_assets"].append(asset_path)

        # Extract fonts section
        fonts_match = re.search(
            r"fonts:\s*\n((?:\s+- family:.*\n(?:\s+.*\n)*)+)",
            pubspec_content,
        )
        if fonts_match:
            fonts_block = fonts_match.group(1)
            for fm in re.finditer(r"- family:\s*(.+)", fonts_block):
                font_name = fm.group(1).strip()
                results["declared_fonts"].append(font_name)

        results["total_declared"] = len(results["declared_assets"])

        # Scan Dart files for asset references
        dart_files = self._collect_dart_files(base, ["lib"])
        used_assets: Set[str] = set()
        referenced_paths: Set[str] = set()

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Find asset references: Image.asset('...'), AssetImage('...'), etc.
            asset_ref_patterns = [
                re.compile(r"""(?:Image\.asset|AssetImage|SvgPicture\.asset)\s*\(\s*['"]([^'"]+)['"]"""),
                re.compile(r"""rootBundle\.load(?:String)?\s*\(\s*['"]([^'"]+)['"]"""),
                re.compile(r"""assets?/[^\s'"]+"""),
            ]

            for pat in asset_ref_patterns:
                for am in pat.finditer(content):
                    ref_path = am.group(1) if am.lastindex else am.group(0)
                    used_assets.add(ref_path)
                    referenced_paths.add(ref_path)

            # Find font references
            font_pat = re.compile(r"""fontFamily:\s*['"]([^'"]+)['"]""")
            for fm in font_pat.finditer(content):
                used_assets.add(f"font:{fm.group(1)}")

        results["used_assets"] = sorted(used_assets)
        results["total_used"] = len(used_assets)

        # Find unused declared assets
        for declared in results["declared_assets"]:
            # Check if any used asset matches (prefix match for directories)
            is_used = any(
                ref.startswith(declared.rstrip("/")) or declared.rstrip("/") in ref
                for ref in referenced_paths
            )
            if not is_used:
                results["unused_assets"].append(declared)

        # Find missing assets (referenced but not declared)
        for ref_path in referenced_paths:
            is_declared = any(
                ref_path.startswith(d.rstrip("/")) or d.rstrip("/") in ref_path
                for d in results["declared_assets"]
            )
            if not is_declared and ref_path.startswith("assets"):
                results["missing_assets"].append(ref_path)

        return results

    # ── 8. verify_route ────────────────────────────────────────────────

    def verify_route(self, route_path: str) -> Dict[str, Any]:
        """
        Verify a route: definition exists, target widget exists, guards configured.

        Args:
            route_path: The route path to verify (e.g., "/home", "/auth/login").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "route_path": route_path,
            "checks": [],
            "issues": [],
            "status": "pass",
        }

        dart_files = self._collect_dart_files(base, ["lib"])
        route_found = False
        target_widget: Optional[str] = None
        route_file: Optional[str] = None
        has_guard = False

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Check for route definition
            path_pat = re.compile(
                rf"""path:\s*['"]""" + re.escape(route_path) + r"""['"]"""
            )
            if path_pat.search(content):
                route_found = True
                route_file = rel_path

                # Find target widget
                # Look for builder after the path definition
                path_pos = path_pat.search(content).start()
                after_path = content[path_pos : path_pos + 500]

                builder_match = re.search(
                    r"(?:builder|pageBuilder)\s*:\s*\([^)]*\)\s*(?:=>|{)\s*(?:return\s+)?(?:const\s+)?(\w+)",
                    after_path,
                )
                if builder_match:
                    target_widget = builder_match.group(1)

                # Check for redirect/guard
                redirect_match = re.search(r"redirect:\s*\(", after_path)
                if redirect_match:
                    has_guard = True

                result["checks"].append({
                    "check": "route_definition",
                    "status": "pass",
                    "file": rel_path,
                    "widget": target_widget,
                })
                break

            # Also check named routes
            named_pat = re.compile(
                rf"""['"]""" + re.escape(route_path) + rf"""['"]\s*:\s*\([^)]*\)\s*(?:=>|{{)\s*(?:return\s+)?(?:const\s+)?(\w+)"""
            )
            nm = named_pat.search(content)
            if nm:
                route_found = True
                route_file = rel_path
                target_widget = nm.group(1)
                result["checks"].append({
                    "check": "named_route_definition",
                    "status": "pass",
                    "file": rel_path,
                    "widget": target_widget,
                })
                break

        if not route_found:
            result["issues"].append({
                "severity": "error",
                "message": f"Route '{route_path}' not found in any route configuration",
            })
            result["status"] = "fail"
            return result

        # Verify target widget exists
        if target_widget:
            widget_found = False
            for fpath, rel_path in dart_files:
                content = self._read_file(fpath)
                if not content:
                    continue
                if re.search(rf"class\s+{re.escape(target_widget)}\s+extends\s+\w+", content):
                    widget_found = True
                    result["checks"].append({
                        "check": "target_widget_exists",
                        "status": "pass",
                        "widget": target_widget,
                        "file": rel_path,
                    })
                    break
            if not widget_found:
                result["issues"].append({
                    "severity": "error",
                    "message": f"Target widget '{target_widget}' not found",
                })
                result["status"] = "fail"

        # Report guard status
        result["checks"].append({
            "check": "guard_configured",
            "status": "pass" if has_guard else "info",
            "message": "Route has redirect guard" if has_guard else "No redirect guard configured",
        })

        if result["issues"]:
            result["status"] = "fail"

        return result

    # ── 9. get_project_conventions ─────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real coding conventions from the Flutter project codebase.

        Args:
            pattern_type: One of: "naming", "state", "architecture", "testing", "localization".
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

        dart_files = self._collect_dart_files(base, ["lib", "test"])

        if pattern_type == "naming":
            result["conventions"] = self._analyze_naming_conventions(base, dart_files)
        elif pattern_type == "state":
            result["conventions"] = self._analyze_state_conventions(base, dart_files)
        elif pattern_type == "architecture":
            result["conventions"] = self._analyze_architecture_conventions(base, dart_files)
        elif pattern_type == "testing":
            result["conventions"] = self._analyze_testing_conventions(base, dart_files)
        elif pattern_type == "localization":
            result["conventions"] = self._analyze_localization_conventions(base, dart_files)

        return result

    def _analyze_naming_conventions(
        self, base: str, dart_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze file and class naming conventions."""
        conventions: Dict[str, Any] = {
            "file_naming": {"snake_case": 0, "camelCase": 0, "kebab-case": 0, "examples": []},
            "class_naming": {"PascalCase": 0, "other": 0, "examples": []},
            "widget_suffix": {"with_suffix": 0, "without_suffix": 0, "suffixes": {}},
        }

        for fpath, rel_path in dart_files:
            fname = os.path.basename(rel_path).replace(".dart", "")
            # File naming
            if "_" in fname and fname == fname.lower():
                conventions["file_naming"]["snake_case"] += 1
            elif fname[0].islower() and any(c.isupper() for c in fname):
                conventions["file_naming"]["camelCase"] += 1
            elif "-" in fname:
                conventions["file_naming"]["kebab-case"] += 1
            else:
                conventions["file_naming"]["snake_case"] += 1

            if len(conventions["file_naming"]["examples"]) < 5:
                conventions["file_naming"]["examples"].append(rel_path)

            # Class naming
            content = self._read_file(fpath)
            if not content:
                continue
            classes = self._extract_class_info(content)
            for cls in classes:
                name = cls["name"]
                if name[0].isupper():
                    conventions["class_naming"]["PascalCase"] += 1
                else:
                    conventions["class_naming"]["other"] += 1

                # Widget suffix patterns
                extends = cls.get("extends", "") or ""
                if "Widget" in extends or "State<" in extends:
                    for suffix in ("Screen", "Page", "View", "Widget", "Card", "Tile", "Dialog", "Sheet"):
                        if name.endswith(suffix):
                            conventions["widget_suffix"]["with_suffix"] += 1
                            conventions["widget_suffix"]["suffixes"][suffix] = (
                                conventions["widget_suffix"]["suffixes"].get(suffix, 0) + 1
                            )
                            break
                    else:
                        conventions["widget_suffix"]["without_suffix"] += 1

        # Determine dominant pattern
        fn = conventions["file_naming"]
        dominant_file = max(("snake_case", fn["snake_case"]), ("camelCase", fn["camelCase"]),
                           ("kebab-case", fn["kebab-case"]), key=lambda x: x[1])
        conventions["file_naming"]["dominant"] = dominant_file[0]

        return conventions

    def _analyze_state_conventions(
        self, base: str, dart_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze state management conventions."""
        conventions: Dict[str, Any] = {
            "frameworks_detected": [],
            "riverpod": {"count": 0, "codegen": False, "auto_dispose": 0},
            "bloc": {"count": 0, "cubits": 0, "blocs": 0},
            "provider": {"count": 0},
            "getx": {"count": 0},
            "setState": {"count": 0, "files": []},
        }

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            # Riverpod
            if re.search(r"(?:flutter_riverpod|hooks_riverpod|riverpod)", content):
                conventions["riverpod"]["count"] += 1
                if "riverpod" not in conventions["frameworks_detected"]:
                    conventions["frameworks_detected"].append("riverpod")
                if "@riverpod" in content or "@Riverpod" in content:
                    conventions["riverpod"]["codegen"] = True
                conventions["riverpod"]["auto_dispose"] += len(
                    re.findall(r"AutoDispose", content)
                )

            # BLoC
            if re.search(r"flutter_bloc|bloc/bloc", content):
                if "bloc" not in conventions["frameworks_detected"]:
                    conventions["frameworks_detected"].append("bloc")
                conventions["bloc"]["count"] += 1
                conventions["bloc"]["cubits"] += len(re.findall(r"extends\s+Cubit<", content))
                conventions["bloc"]["blocs"] += len(re.findall(r"extends\s+Bloc<", content))

            # Provider package
            if "package:provider/" in content:
                if "provider" not in conventions["frameworks_detected"]:
                    conventions["frameworks_detected"].append("provider")
                conventions["provider"]["count"] += 1

            # GetX
            if "package:get/" in content or "GetxController" in content:
                if "getx" not in conventions["frameworks_detected"]:
                    conventions["frameworks_detected"].append("getx")
                conventions["getx"]["count"] += 1

            # Raw setState
            set_state_count = len(re.findall(r"setState\s*\(", content))
            if set_state_count > 0:
                conventions["setState"]["count"] += set_state_count
                if len(conventions["setState"]["files"]) < 10:
                    conventions["setState"]["files"].append(rel_path)

        return conventions

    def _analyze_architecture_conventions(
        self, base: str, dart_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze architecture patterns."""
        conventions: Dict[str, Any] = {
            "structure": "unknown",
            "directories": {},
            "patterns_detected": [],
        }

        # Check directory structure
        lib_path = os.path.join(base, "lib")
        if os.path.isdir(lib_path):
            for item in os.listdir(lib_path):
                item_path = os.path.join(lib_path, item)
                if os.path.isdir(item_path):
                    file_count = sum(
                        1 for _, _, files in os.walk(item_path)
                        for f in files if f.endswith(".dart")
                    )
                    conventions["directories"][item] = file_count

        # Detect architecture patterns
        dirs = set(conventions["directories"].keys())

        # Clean Architecture
        if {"domain", "data", "presentation"} & dirs:
            conventions["patterns_detected"].append("clean_architecture")
            conventions["structure"] = "clean_architecture"

        # Feature-first
        if "features" in dirs or "feature" in dirs:
            conventions["patterns_detected"].append("feature_first")
            if conventions["structure"] == "unknown":
                conventions["structure"] = "feature_first"

        # Layer-first
        layer_dirs = {"models", "views", "controllers", "services", "repositories"}
        if len(layer_dirs & dirs) >= 3:
            conventions["patterns_detected"].append("layer_first")
            if conventions["structure"] == "unknown":
                conventions["structure"] = "layer_first"

        # MVVM
        if {"view", "viewmodel", "model"} & dirs or {"views", "view_models", "models"} & dirs:
            conventions["patterns_detected"].append("mvvm")
            if conventions["structure"] == "unknown":
                conventions["structure"] = "mvvm"

        # MVC
        if {"views", "controllers", "models"} & dirs:
            conventions["patterns_detected"].append("mvc")

        if conventions["structure"] == "unknown":
            conventions["structure"] = "flat" if len(dirs) <= 3 else "custom"

        return conventions

    def _analyze_testing_conventions(
        self, base: str, dart_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze testing conventions."""
        conventions: Dict[str, Any] = {
            "widget_tests": 0,
            "unit_tests": 0,
            "integration_tests": 0,
            "golden_tests": 0,
            "test_frameworks": [],
            "mocking": [],
            "test_files": [],
        }

        test_dir = os.path.join(base, "test")
        integration_dir = os.path.join(base, "integration_test")

        # Scan test directory
        if os.path.isdir(test_dir):
            for root, _, files in os.walk(test_dir):
                for fname in files:
                    if not fname.endswith("_test.dart"):
                        continue
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, base)
                    content = self._read_file(fpath)
                    if not content:
                        continue

                    if "testWidgets(" in content or "pumpWidget(" in content:
                        conventions["widget_tests"] += 1
                    elif "test(" in content or "group(" in content:
                        conventions["unit_tests"] += 1

                    if "matchesGoldenFile" in content or "goldenFileComparator" in content:
                        conventions["golden_tests"] += 1

                    # Detect test frameworks
                    if "package:mockito/" in content and "mockito" not in conventions["mocking"]:
                        conventions["mocking"].append("mockito")
                    if "package:mocktail/" in content and "mocktail" not in conventions["mocking"]:
                        conventions["mocking"].append("mocktail")
                    if "package:bloc_test/" in content and "bloc_test" not in conventions["test_frameworks"]:
                        conventions["test_frameworks"].append("bloc_test")

                    if len(conventions["test_files"]) < 10:
                        conventions["test_files"].append(rel_path)

        # Scan integration tests
        if os.path.isdir(integration_dir):
            for root, _, files in os.walk(integration_dir):
                for fname in files:
                    if fname.endswith("_test.dart"):
                        conventions["integration_tests"] += 1

        return conventions

    def _analyze_localization_conventions(
        self, base: str, dart_files: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """Analyze localization / i18n conventions."""
        conventions: Dict[str, Any] = {
            "method": "unknown",
            "languages": [],
            "hardcoded_strings": 0,
            "hardcoded_files": [],
        }

        # Check for ARB files
        l10n_dir = os.path.join(base, "lib", "l10n")
        arb_files: List[str] = []
        for search_dir in [l10n_dir, os.path.join(base, "lib"), base]:
            if os.path.isdir(search_dir):
                for root, _, files in os.walk(search_dir):
                    for fname in files:
                        if fname.endswith(".arb"):
                            arb_files.append(os.path.relpath(os.path.join(root, fname), base))
                            # Extract locale from filename
                            locale_match = re.search(r"_(\w{2}(?:_\w{2})?)\.arb$", fname)
                            if locale_match:
                                conventions["languages"].append(locale_match.group(1))

        if arb_files:
            conventions["method"] = "flutter_localizations"
            conventions["arb_files"] = arb_files

        # Check for easy_localization
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue
            if "package:easy_localization/" in content:
                conventions["method"] = "easy_localization"
                break
            if "package:intl/" in content and conventions["method"] == "unknown":
                conventions["method"] = "intl"

        # Find hardcoded strings in widgets (potential l10n issues)
        for fpath, rel_path in dart_files:
            if "test" in rel_path or "l10n" in rel_path or ".g.dart" in rel_path:
                continue
            content = self._read_file(fpath)
            if not content:
                continue
            # Find Text('hardcoded string') patterns
            hardcoded = re.findall(
                r"""(?:Text|title|label|hint|tooltip)\s*[:(\s]+\s*['"]([^'"]{3,})['"]""",
                content,
            )
            if hardcoded:
                conventions["hardcoded_strings"] += len(hardcoded)
                if len(conventions["hardcoded_files"]) < 10:
                    conventions["hardcoded_files"].append({
                        "file": rel_path,
                        "count": len(hardcoded),
                        "examples": hardcoded[:3],
                    })

        return conventions

    # ── 10. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.

        Args:
            description: Natural language description (e.g., "form validation", "bottom sheet").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results: Dict[str, Any] = {"query": description, "matches": [], "total": 0}

        # Determine which patterns to search for
        desc_lower = description.lower()
        matched_patterns: List[Tuple[str, Dict[str, Any]]] = []
        for pattern_name, pattern_info in self.SIMILAR_PATTERN_REGISTRY.items():
            score = sum(1 for kw in pattern_info["keywords"] if kw in desc_lower)
            if score > 0:
                matched_patterns.append((pattern_name, pattern_info))

        if not matched_patterns:
            # Fall back to simple text search
            matched_patterns = list(self.SIMILAR_PATTERN_REGISTRY.items())

        dart_files = self._collect_dart_files(base, ["lib"])

        for pattern_name, pattern_info in matched_patterns:
            file_matches: List[Dict[str, Any]] = []
            for fpath, rel_path in dart_files:
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
        Generate a compact snapshot of the Flutter project structure.

        Returns: screens, widgets by type, providers/blocs, routes, assets,
        pubspec dependencies, platform folders (android/ios/web).
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "project_name": "unknown",
            "screens": [],
            "widgets": {"stateless": 0, "stateful": 0, "consumer": 0, "hook": 0, "other": 0},
            "state_management": {"providers": 0, "blocs": 0, "cubits": 0},
            "routes": 0,
            "models": 0,
            "services": 0,
            "repositories": 0,
            "assets": {"declared": 0, "fonts": 0},
            "dependencies": {},
            "dev_dependencies": {},
            "platforms": [],
            "dart_files_count": 0,
            "test_files_count": 0,
            "directory_structure": {},
        }

        # Parse pubspec.yaml
        pubspec_path = os.path.join(base, "pubspec.yaml")
        pubspec_content = self._read_file(pubspec_path)
        if pubspec_content:
            name_match = re.search(r"^name:\s*(.+)", pubspec_content, re.MULTILINE)
            if name_match:
                snapshot["project_name"] = name_match.group(1).strip()

            # Dependencies
            dep_section = re.search(
                r"^dependencies:\s*\n((?:\s+\S.*\n?)*)", pubspec_content, re.MULTILINE
            )
            if dep_section:
                for dm in re.finditer(r"^\s+(\w[\w_-]*):", dep_section.group(1), re.MULTILINE):
                    dep_name = dm.group(1)
                    snapshot["dependencies"][dep_name] = True

            dev_dep_section = re.search(
                r"^dev_dependencies:\s*\n((?:\s+\S.*\n?)*)", pubspec_content, re.MULTILINE
            )
            if dev_dep_section:
                for dm in re.finditer(r"^\s+(\w[\w_-]*):", dev_dep_section.group(1), re.MULTILINE):
                    dep_name = dm.group(1)
                    snapshot["dev_dependencies"][dep_name] = True

            # Assets count
            assets_match = re.search(r"assets:\s*\n((?:\s+-\s+[^\n]+\n?)+)", pubspec_content)
            if assets_match:
                snapshot["assets"]["declared"] = len(
                    re.findall(r"-\s+", assets_match.group(1))
                )

            fonts_match = re.search(r"fonts:\s*\n((?:\s+- family:.*\n(?:\s+.*\n)*)+)", pubspec_content)
            if fonts_match:
                snapshot["assets"]["fonts"] = len(
                    re.findall(r"- family:", fonts_match.group(1))
                )

        # Check platform folders
        for platform in ["android", "ios", "web", "macos", "linux", "windows"]:
            if os.path.isdir(os.path.join(base, platform)):
                snapshot["platforms"].append(platform)

        # Directory structure
        lib_path = os.path.join(base, "lib")
        if os.path.isdir(lib_path):
            for item in sorted(os.listdir(lib_path)):
                item_path = os.path.join(lib_path, item)
                if os.path.isdir(item_path):
                    count = sum(
                        1 for _, _, files in os.walk(item_path)
                        for f in files if f.endswith(".dart")
                    )
                    snapshot["directory_structure"][item] = count

        # Scan Dart files
        dart_files = self._collect_dart_files(base, ["lib"])
        snapshot["dart_files_count"] = len(dart_files)

        test_files = self._collect_dart_files(base, ["test"])
        snapshot["test_files_count"] = len(test_files)

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            classes = self._extract_class_info(content)
            for cls in classes:
                extends = cls.get("extends", "") or ""
                name = cls["name"]

                # Count widget types
                if "StatelessWidget" in extends:
                    snapshot["widgets"]["stateless"] += 1
                elif "StatefulWidget" in extends:
                    snapshot["widgets"]["stateful"] += 1
                elif "ConsumerWidget" in extends or "ConsumerStatefulWidget" in extends:
                    snapshot["widgets"]["consumer"] += 1
                elif "HookWidget" in extends or "HookConsumerWidget" in extends:
                    snapshot["widgets"]["hook"] += 1
                elif "Widget" in extends:
                    snapshot["widgets"]["other"] += 1

                # Count screens
                if name.endswith(("Screen", "Page")) and "Widget" in extends:
                    snapshot["screens"].append({"name": name, "file": rel_path})

                # Count blocs/cubits
                if "Bloc<" in extends:
                    snapshot["state_management"]["blocs"] += 1
                elif "Cubit<" in extends:
                    snapshot["state_management"]["cubits"] += 1

                # Count services/repositories
                if name.endswith("Service"):
                    snapshot["services"] += 1
                elif name.endswith("Repository"):
                    snapshot["repositories"] += 1

            # Count riverpod providers
            provider_count = len(re.findall(
                r"(?:final|const)\s+\w+\s*=\s*(?:Provider|StateNotifierProvider|"
                r"FutureProvider|StreamProvider|NotifierProvider|StateProvider)",
                content,
            ))
            snapshot["state_management"]["providers"] += provider_count

            # Count routes
            route_count = len(re.findall(r"GoRoute\s*\(|AutoRoute\s*\(", content))
            snapshot["routes"] += route_count

            # Count models (files in models dir with fromJson)
            if "/models/" in rel_path or "/entities/" in rel_path:
                if "fromJson" in content or "@freezed" in content or "@JsonSerializable" in content:
                    snapshot["models"] += 1

        # Limit screens list
        snapshot["screens"] = snapshot["screens"][:30]

        return snapshot

    # ── 12. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit impact check for Flutter files.

        widget -> check parent/child tree
        provider -> check consumers
        model -> check serialization/repository

        Args:
            file_path: Relative file path (e.g., "lib/screens/home_screen.dart").
            symbol_name: Symbol to check (e.g., "HomeScreen", "userProvider", "User").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "file_path": file_path,
            "symbol_name": symbol_name,
            "symbol_type": "unknown",
            "impact": [],
            "warnings": [],
            "references": [],
        }

        target_path = os.path.join(base, file_path)
        target_content = self._read_file(target_path)
        if not target_content:
            return {"status": "error", "message": f"File not found: {file_path}"}

        dart_files = self._collect_dart_files(base, ["lib", "test"])

        # Determine symbol type
        # Widget?
        widget_match = re.search(
            rf"class\s+{re.escape(symbol_name)}\s+extends\s+(\w+)",
            target_content,
        )
        if widget_match:
            extends = widget_match.group(1)
            if "Widget" in extends or "State<" in extends:
                result["symbol_type"] = "widget"
                self._check_widget_impact(symbol_name, extends, dart_files, base, result)
                return result

        # Provider?
        provider_match = re.search(
            rf"(?:final|const)\s+{re.escape(symbol_name)}\s*=\s*(\w+Provider)",
            target_content,
        )
        if provider_match:
            result["symbol_type"] = "provider"
            self._check_provider_impact(symbol_name, dart_files, base, result)
            return result

        # BLoC/Cubit?
        bloc_match = re.search(
            rf"class\s+{re.escape(symbol_name)}\s+extends\s+(?:Bloc|Cubit)<",
            target_content,
        )
        if bloc_match:
            result["symbol_type"] = "bloc"
            self._check_bloc_impact(symbol_name, dart_files, base, result)
            return result

        # Model?
        model_match = re.search(
            rf"class\s+{re.escape(symbol_name)}\b",
            target_content,
        )
        if model_match and (
            "fromJson" in target_content or "@freezed" in target_content or
            "@JsonSerializable" in target_content
        ):
            result["symbol_type"] = "model"
            self._check_model_impact(symbol_name, dart_files, base, result)
            return result

        # Generic symbol - find all references
        result["symbol_type"] = "generic"
        for fpath, rel_path in dart_files:
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

    def _check_widget_impact(
        self, widget_name: str, extends: str, dart_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a widget."""
        # Find parent widgets that use this widget
        parents: List[Dict[str, Any]] = []
        consumers: List[Dict[str, Any]] = []

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content or widget_name not in content:
                continue

            # Check for widget usage in build methods
            if re.search(rf"\b{re.escape(widget_name)}\s*\(", content):
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if re.search(rf"\b{re.escape(widget_name)}\s*\(", line):
                        parents.append({
                            "file": rel_path,
                            "line": i,
                            "text": line.strip()[:120],
                        })

            # Check for imports
            if re.search(rf"""import\s+['"][^'"]*['"].*{re.escape(widget_name)}""", content):
                consumers.append({"file": rel_path, "type": "import"})

        result["impact"].append({
            "type": "parent_widgets",
            "count": len(parents),
            "items": parents[:15],
        })

        # Check if widget is registered in routes
        route_refs: List[Dict[str, Any]] = []
        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue
            if re.search(rf"(?:builder|pageBuilder|page).*{re.escape(widget_name)}", content):
                route_refs.append({"file": rel_path})
        if route_refs:
            result["impact"].append({
                "type": "route_registration",
                "count": len(route_refs),
                "items": route_refs[:5],
            })
            result["warnings"].append(
                f"Widget '{widget_name}' is registered in routes. Renaming/removing will break navigation."
            )

    def _check_provider_impact(
        self, provider_name: str, dart_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a provider."""
        consumers: List[Dict[str, Any]] = []

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content or provider_name not in content:
                continue

            # ref.watch/read/listen
            for m in re.finditer(rf"ref\.(watch|read|listen)\s*\(\s*{re.escape(provider_name)}", content):
                lines = content.split("\n")
                line_num = content[:m.start()].count("\n") + 1
                consumers.append({
                    "file": rel_path,
                    "line": line_num,
                    "usage": m.group(1),
                    "text": lines[line_num - 1].strip()[:120] if line_num <= len(lines) else "",
                })

        result["impact"].append({
            "type": "consumers",
            "count": len(consumers),
            "items": consumers[:20],
        })

        if len(consumers) > 5:
            result["warnings"].append(
                f"Provider '{provider_name}' has {len(consumers)} consumers. Changes will propagate widely."
            )

    def _check_bloc_impact(
        self, bloc_name: str, dart_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a BLoC/Cubit."""
        consumers: List[Dict[str, Any]] = []

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content or bloc_name not in content:
                continue

            # BlocBuilder, BlocListener, BlocConsumer, context.read/watch
            patterns = [
                (rf"(?:BlocBuilder|BlocListener|BlocConsumer|BlocSelector)<{re.escape(bloc_name)}", "widget"),
                (rf"context\.(read|watch)<{re.escape(bloc_name)}>", "context"),
                (rf"BlocProvider<{re.escape(bloc_name)}>", "provider"),
            ]
            for pat, usage_type in patterns:
                for m in re.finditer(pat, content):
                    line_num = content[:m.start()].count("\n") + 1
                    consumers.append({
                        "file": rel_path,
                        "line": line_num,
                        "usage": usage_type,
                    })

        result["impact"].append({
            "type": "consumers",
            "count": len(consumers),
            "items": consumers[:20],
        })

        if len(consumers) > 3:
            result["warnings"].append(
                f"BLoC '{bloc_name}' has {len(consumers)} UI consumers. State changes affect all."
            )

    def _check_model_impact(
        self, model_name: str, dart_files: List[Tuple[str, str]],
        base: str, result: Dict[str, Any],
    ) -> None:
        """Check impact of editing a model class."""
        usages: List[Dict[str, Any]] = []
        serialization_usages: List[Dict[str, Any]] = []
        repo_usages: List[Dict[str, Any]] = []

        for fpath, rel_path in dart_files:
            content = self._read_file(fpath)
            if not content or model_name not in content:
                continue

            # fromJson / toJson usage
            if re.search(rf"{re.escape(model_name)}\.fromJson", content):
                serialization_usages.append({"file": rel_path, "type": "fromJson"})
            if re.search(rf"\.toJson\(\)", content) and model_name in content:
                serialization_usages.append({"file": rel_path, "type": "toJson"})

            # Repository usage
            if "Repository" in rel_path or "repository" in rel_path:
                repo_usages.append({"file": rel_path})

            # General usage
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if model_name in line:
                    usages.append({
                        "file": rel_path,
                        "line": i,
                        "text": line.strip()[:120],
                    })

        result["impact"].append({
            "type": "serialization",
            "count": len(serialization_usages),
            "items": serialization_usages[:10],
        })
        result["impact"].append({
            "type": "repository",
            "count": len(repo_usages),
            "items": repo_usages[:10],
        })
        result["impact"].append({
            "type": "general_references",
            "count": len(usages),
            "items": usages[:15],
        })

        if serialization_usages:
            result["warnings"].append(
                f"Model '{model_name}' has serialization (fromJson/toJson). "
                "Field changes may break API contract."
            )
        if repo_usages:
            result["warnings"].append(
                f"Model '{model_name}' is used in {len(repo_usages)} repository files. "
                "Check data layer compatibility."
            )

    # ── verify_schema ────────────────────────────────────────────────

    def verify_schema(self, model_name: str, fields: list) -> Dict[str, Any]:
        """Verify Dart model class fields and fromJson/toJson field mappings."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "model": model_name,
            "expected_fields": fields,
            "class_fields": [],
            "from_json_fields": [],
            "to_json_fields": [],
            "mismatches": [],
        }

        dart_files = self._collect_dart_files(base, ["lib"])

        class_pattern = re.compile(
            rf"class\s+{re.escape(model_name)}\b[^{{]*\{{(.*?)\n\}}",
            re.DOTALL,
        )
        # Dart field: final Type name; or Type name; or Type? name;
        field_pattern = re.compile(
            r"(?:final\s+)?(?:\w+[?]?\s+)(\w+)\s*;",
        )

        for fpath, rel in dart_files:
            content = self._read_file(fpath)
            if not content:
                continue

            cm = class_pattern.search(content)
            if not cm:
                continue

            body = cm.group(1)

            # Extract class fields
            for fm in field_pattern.finditer(body):
                fname = fm.group(1)
                if not fname.startswith("_") and fname not in result["class_fields"]:
                    result["class_fields"].append(fname)

            # Extract constructor named params as fields too
            constructor_match = re.search(
                rf"{re.escape(model_name)}\s*\(\s*\{{(.*?)\}}\s*\)",
                body, re.DOTALL,
            )
            if constructor_match:
                ctor_body = constructor_match.group(1)
                ctor_fields = re.findall(r"(?:required\s+)?(?:this\.)?(\w+)", ctor_body)
                for cf in ctor_fields:
                    if cf not in result["class_fields"] and cf not in ("required", "super", "key"):
                        result["class_fields"].append(cf)

            # Extract fromJson field mappings
            from_json_match = re.search(
                rf"(?:factory\s+)?{re.escape(model_name)}\.fromJson\s*\([^)]*\)\s*(?:=>|:|\{{)(.*?)(?:\}}\s*;|\);)",
                body, re.DOTALL,
            )
            if from_json_match:
                fj_body = from_json_match.group(1)
                # json['field'] or json["field"] or map['field']
                json_keys = re.findall(r"(?:json|map|data)\s*\[\s*['\"](\w+)['\"]\s*\]", fj_body)
                result["from_json_fields"] = list(dict.fromkeys(json_keys))

            # Extract toJson field mappings
            to_json_match = re.search(
                r"(?:Map<String,\s*dynamic>|Map)\s+toJson\s*\(\s*\)\s*(?:=>|{)(.*?)(?:};|;)",
                body, re.DOTALL,
            )
            if to_json_match:
                tj_body = to_json_match.group(1)
                json_keys = re.findall(r"['\"](\w+)['\"]\s*:", tj_body)
                result["to_json_fields"] = list(dict.fromkeys(json_keys))

        # Check mismatches
        expected_set = set(fields)
        class_set = set(result["class_fields"])
        fj_set = set(result["from_json_fields"])
        tj_set = set(result["to_json_fields"])

        for field in fields:
            if class_set and field not in class_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_class",
                    "message": f"Field '{field}' not found in class definition",
                })
            if fj_set and field not in fj_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_from_json",
                    "message": f"Field '{field}' not mapped in fromJson",
                })
            if tj_set and field not in tj_set:
                result["mismatches"].append({
                    "field": field, "issue": "missing_in_to_json",
                    "message": f"Field '{field}' not mapped in toJson",
                })

        # Cross-check fromJson vs toJson
        if fj_set and tj_set:
            for fj_field in fj_set - tj_set:
                result["mismatches"].append({
                    "field": fj_field, "issue": "in_from_json_not_to_json",
                    "message": f"Field '{fj_field}' is in fromJson but missing from toJson",
                })
            for tj_field in tj_set - fj_set:
                result["mismatches"].append({
                    "field": tj_field, "issue": "in_to_json_not_from_json",
                    "message": f"Field '{tj_field}' is in toJson but missing from fromJson",
                })

        result["status"] = "ok" if not result["mismatches"] else "mismatches_found"
        return result

    # ── detect_transaction_risks ─────────────────────────────────────

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing database transactions in repository methods with multiple writes."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        risks: List[Dict[str, Any]] = []

        # Find method boundaries
        method_pattern = re.compile(
            r"(?:Future<[^>]*>|void|Future)\s+(\w+)\s*\([^)]*\)\s*(?:async\s*)?\{",
        )
        methods = list(method_pattern.finditer(content))

        for idx, mm in enumerate(methods):
            method_name = mm.group(1)
            start = mm.start()
            end = methods[idx + 1].start() if idx + 1 < len(methods) else len(content)
            body = content[start:end]

            # Count DB write operations
            db_writes = re.findall(
                r"(?:db|database|_db|dao)\s*\.\s*(?:insert|update|delete|rawInsert|rawUpdate|rawDelete|execute|batch)\s*\(",
                body,
            )
            # Also check for sqflite-style operations
            sqflite_writes = re.findall(
                r"(?:insert|update|delete|rawInsert|rawUpdate|rawDelete)\s*\(\s*['\"]",
                body,
            )
            # Drift/Moor style
            drift_writes = re.findall(
                r"(?:into|update|deleteWhere|customInsert|customUpdate)\s*\(\s*",
                body,
            )

            total_writes = len(db_writes) + len(sqflite_writes) + len(drift_writes)

            if total_writes >= 2:
                has_transaction = bool(re.search(
                    r"(?:\.transaction\s*\(|batch\s*\(\s*\(|\.batch\s*\(|runInTransaction)",
                    body,
                ))
                if not has_transaction:
                    line_num = content[:start].count("\n") + 1
                    risks.append({
                        "method": method_name,
                        "line": line_num,
                        "write_operations": total_writes,
                        "risk": "multiple_writes_without_transaction",
                        "message": f"Method '{method_name}' has {total_writes} DB write operations without a transaction",
                        "suggestion": "Wrap in db.transaction(() async { ... }) or use batch operations",
                    })

            # Check for await inside loops (N+1 risk)
            loop_await = re.findall(
                r"(?:for|while)\s*\([^)]*\)\s*\{[^}]*await\s+.*?(?:insert|update|delete|rawInsert|rawUpdate|rawDelete)\s*\(",
                body, re.DOTALL,
            )
            if loop_await:
                line_num = content[:start].count("\n") + 1
                risks.append({
                    "method": method_name,
                    "line": line_num,
                    "risk": "db_writes_in_loop",
                    "message": f"Method '{method_name}' has DB write operations inside a loop -- use batch operations",
                    "suggestion": "Use db.batch() or collect operations and execute in a single transaction",
                })

        return {"status": "ok", "file": file_path, "risks": risks, "total_risks": len(risks)}

    # ── get_domain_rules ─────────────────────────────────────────────

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check Flutter domain-specific rules for file types."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        violations: List[Dict[str, Any]] = []
        file_type = "unknown"
        rel_path = os.path.relpath(full_path, base) if base else file_path

        # Detect file type from content and path
        is_screen = bool(re.search(
            r"(?:Screen|Page|View)\s*extends\s*(?:Stateful|Stateless)Widget",
            content,
        )) or re.search(r"(?:screens?|pages?|views?)/", rel_path, re.IGNORECASE)

        is_bloc = bool(re.search(r"extends\s+(?:Bloc|Cubit)\b", content))
        is_repo = bool(re.search(r"(?:repository|repo)/", rel_path, re.IGNORECASE)) or \
                  bool(re.search(r"class\s+\w*Repository\b", content))
        is_provider = bool(re.search(
            r"(?:extends\s+ChangeNotifier|StateNotifier|@riverpod|final\s+\w+Provider\s*=)",
            content,
        ))

        if is_screen:
            file_type = "screen"
            # Rule: screens with StatefulWidget require dispose cleanup
            stateful_classes = re.finditer(
                r"class\s+_(\w+State)\s+extends\s+State<\w+>\s*\{(.*?)(?=\nclass\s|\Z)",
                content, re.DOTALL,
            )
            for sm in stateful_classes:
                state_name = sm.group(1)
                state_body = sm.group(2)

                # Check for controllers/subscriptions without dispose
                controllers = re.findall(
                    r"(?:TextEditingController|ScrollController|AnimationController|TabController|FocusNode|StreamSubscription)\s+",
                    state_body,
                )
                has_dispose = bool(re.search(r"@override\s*\n\s*void\s+dispose\s*\(", state_body))

                if controllers and not has_dispose:
                    violations.append({
                        "rule": "screen_requires_dispose",
                        "severity": "error",
                        "class": state_name,
                        "controllers": len(controllers),
                        "message": f"State class '{state_name}' has {len(controllers)} controller(s) but no dispose() override -- memory leak risk",
                    })

        elif is_bloc:
            file_type = "bloc"
            # Rule: BLoC/Cubit must have error states
            bloc_classes = re.finditer(
                r"class\s+(\w+)\s+extends\s+(?:Bloc|Cubit)<(\w+)>\s*\{(.*?)(?=\nclass\s|\Z)",
                content, re.DOTALL,
            )
            for bm in bloc_classes:
                bloc_name = bm.group(1)
                state_type = bm.group(2)
                bloc_body = bm.group(3)

                # Check if error state is emitted
                has_error_emit = bool(re.search(
                    r"emit\s*\(\s*\w*(?:Error|Failure|Failed)\w*\s*\(",
                    bloc_body,
                ))
                has_try_catch = "try" in bloc_body and "catch" in bloc_body

                if not has_error_emit:
                    violations.append({
                        "rule": "bloc_requires_error_state",
                        "severity": "warning",
                        "class": bloc_name,
                        "message": f"BLoC '{bloc_name}' never emits an error state -- add error handling with error state emission",
                    })
                if not has_try_catch:
                    violations.append({
                        "rule": "bloc_requires_try_catch",
                        "severity": "warning",
                        "class": bloc_name,
                        "message": f"BLoC '{bloc_name}' lacks try-catch blocks -- async operations should handle exceptions",
                    })

        elif is_repo:
            file_type = "repository"
            # Rule: repository methods require try-catch
            method_pattern = re.compile(
                r"(?:Future<[^>]*>|Stream<[^>]*>)\s+(\w+)\s*\([^)]*\)\s*(?:async\s*)?\{(.*?)(?=\n\s*(?:Future|Stream|void|static|@|\}))",
                content, re.DOTALL,
            )
            for mm in method_pattern.finditer(content):
                method_name = mm.group(1)
                method_body = mm.group(2)
                if method_name.startswith("_"):
                    continue
                if "try" not in method_body or "catch" not in method_body:
                    violations.append({
                        "rule": "repository_requires_try_catch",
                        "severity": "warning",
                        "method": method_name,
                        "message": f"Repository method '{method_name}' lacks try-catch -- network/DB errors will propagate unhandled",
                    })

        elif is_provider:
            file_type = "provider"
            # Rule: providers require error handling
            provider_methods = re.finditer(
                r"(?:Future<[^>]*>|void)\s+(\w+)\s*\([^)]*\)\s*(?:async\s*)?\{(.*?)(?=\n\s*(?:Future|void|static|@|\}))",
                content, re.DOTALL,
            )
            for pm in provider_methods.finditer(content) if hasattr(provider_methods, 'finditer') else provider_methods:
                method_name = pm.group(1)
                method_body = pm.group(2)
                if method_name.startswith("_"):
                    continue
                has_error_handling = "try" in method_body and "catch" in method_body
                has_error_state = bool(re.search(r"(?:state\s*=|_error|hasError|errorMessage)", method_body))
                if not has_error_handling:
                    violations.append({
                        "rule": "provider_requires_error_handling",
                        "severity": "warning",
                        "method": method_name,
                        "message": f"Provider method '{method_name}' lacks error handling -- wrap in try-catch and update error state",
                    })

        return {
            "status": "ok",
            "file": file_path,
            "file_type": file_type,
            "violations": violations,
            "total_violations": len(violations),
        }

    # ── generate_test_skeleton ───────────────────────────────────────

    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Generate Flutter test skeleton: widget test, unit test, or mock setup."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {"file": file_path, "symbol": symbol, "skeleton": "", "test_type": "unknown"}
        rel_path = os.path.relpath(full_path, base) if base else file_path
        import_path = rel_path.replace("lib/", "").replace(os.sep, "/")
        package_name = "app"  # default

        # Try to get package name from pubspec
        pubspec_path = os.path.join(base, "pubspec.yaml")
        if os.path.isfile(pubspec_path):
            pubspec = self._read_file(pubspec_path) or ""
            pkg_match = re.search(r"^name:\s*(\S+)", pubspec, re.MULTILINE)
            if pkg_match:
                package_name = pkg_match.group(1)

        # Check if symbol is a widget
        is_widget = bool(re.search(
            rf"class\s+{re.escape(symbol)}\s+extends\s+(?:Stateless|Stateful|HookWidget|Consumer)Widget",
            content,
        ))
        is_bloc = bool(re.search(rf"class\s+{re.escape(symbol)}\s+extends\s+(?:Bloc|Cubit)", content))

        # Extract constructor params for the widget
        ctor_params = []
        ctor_match = re.search(
            rf"(?:const\s+)?{re.escape(symbol)}\s*\(\s*\{{([^}}]*)\}}\s*\)",
            content, re.DOTALL,
        )
        if ctor_match:
            raw_params = ctor_match.group(1)
            ctor_params = re.findall(r"(?:required\s+)?(?:this\.)?(\w+)", raw_params)
            ctor_params = [p for p in ctor_params if p not in ("required", "super", "key", "Key")]

        # Extract dependencies (constructor injections for non-widgets)
        deps = re.findall(
            rf"(?:final\s+)?(\w+Repository|\w+Service|\w+UseCase|\w+Api|\w+Client)\s+(?:_)?(\w+)\s*;",
            content,
        )

        if is_widget:
            result["test_type"] = "widget"
            props_str = ", ".join(f"{p}: TODO" for p in ctor_params[:6]) if ctor_params else ""
            mock_setup = ""
            if deps:
                mock_classes = "\n".join(f"class Mock{d[0]} extends Mock implements {d[0]} {{}}" for d in deps)
                mock_vars = "\n".join(f"  late Mock{d[0]} mock{d[0]};" for d in deps)
                mock_inits = "\n".join(f"    mock{d[0]} = Mock{d[0]}();" for d in deps)
                mock_setup = f"\n{mock_classes}\n"

            result["skeleton"] = (
                f"import 'package:flutter_test/flutter_test.dart';\n"
                f"import 'package:mockito/mockito.dart';\n"
                f"import 'package:{package_name}/{import_path}';\n"
                f"{mock_setup}\n"
                f"void main() {{\n"
                f"  group('{symbol}', () {{\n"
                f"    testWidgets('should render successfully', (WidgetTester tester) async {{\n"
                f"      await tester.pumpWidget(\n"
                f"        MaterialApp(home: {symbol}({props_str})),\n"
                f"      );\n"
                f"      expect(find.byType({symbol}), findsOneWidget);\n"
                f"    }});\n\n"
                f"    testWidgets('should display expected content', (WidgetTester tester) async {{\n"
                f"      await tester.pumpWidget(\n"
                f"        MaterialApp(home: {symbol}({props_str})),\n"
                f"      );\n"
                f"      // TODO: Add specific widget content assertions\n"
                f"      await tester.pump();\n"
                f"    }});\n"
                f"  }});\n"
                f"}}\n"
            )

        elif is_bloc:
            result["test_type"] = "bloc"
            # Find state type and events
            bloc_match = re.search(
                rf"class\s+{re.escape(symbol)}\s+extends\s+(?:Bloc|Cubit)<(\w+)>",
                content,
            )
            state_type = bloc_match.group(1) if bloc_match else "dynamic"
            events = re.findall(r"on<(\w+)>\s*\(", content)

            mock_classes = "\n".join(f"class Mock{d[0]} extends Mock implements {d[0]} {{}}" for d in deps)
            mock_vars = "\n".join(f"  late Mock{d[0]} mock{d[0]};" for d in deps)
            mock_inits = "\n".join(f"    mock{d[0]} = Mock{d[0]}();" for d in deps)

            event_tests = "\n\n".join(
                f"    test('should handle {e}', () {{\n"
                f"      // TODO: stub dependencies\n"
                f"      bloc.add({e}());\n"
                f"      // TODO: expectLater with emitsInOrder\n"
                f"    }});"
                for e in events[:5]
            )

            result["skeleton"] = (
                f"import 'package:flutter_test/flutter_test.dart';\n"
                f"import 'package:bloc_test/bloc_test.dart';\n"
                f"import 'package:mockito/mockito.dart';\n"
                f"import 'package:{package_name}/{import_path}';\n\n"
                f"{mock_classes}\n\n"
                f"void main() {{\n"
                f"  group('{symbol}', () {{\n"
                f"{mock_vars}\n"
                f"    late {symbol} bloc;\n\n"
                f"    setUp(() {{\n"
                f"{mock_inits}\n"
                f"      bloc = {symbol}({', '.join(f'mock{d[0]}' for d in deps)});\n"
                f"    }});\n\n"
                f"    tearDown(() => bloc.close());\n\n"
                f"    test('initial state is correct', () {{\n"
                f"      expect(bloc.state, isA<{state_type}>());\n"
                f"    }});\n\n"
                f"{event_tests}\n"
                f"  }});\n"
                f"}}\n"
            )
        else:
            result["test_type"] = "unit"
            mock_classes = "\n".join(f"class Mock{d[0]} extends Mock implements {d[0]} {{}}" for d in deps)
            mock_vars = "\n".join(f"  late Mock{d[0]} mock{d[0]};" for d in deps)
            mock_inits = "\n".join(f"    mock{d[0]} = Mock{d[0]}();" for d in deps)
            ctor_args = ", ".join(f"mock{d[0]}" for d in deps) if deps else ""

            result["skeleton"] = (
                f"import 'package:flutter_test/flutter_test.dart';\n"
                f"import 'package:mockito/mockito.dart';\n"
                f"import 'package:{package_name}/{import_path}';\n\n"
                f"{mock_classes}\n\n"
                f"void main() {{\n"
                f"  group('{symbol}', () {{\n"
                f"{mock_vars}\n"
                f"    late {symbol} sut;\n\n"
                f"    setUp(() {{\n"
                f"{mock_inits}\n"
                f"      sut = {symbol}({ctor_args});\n"
                f"    }});\n\n"
                f"    test('should be created', () {{\n"
                f"      expect(sut, isNotNull);\n"
                f"    }});\n\n"
                f"    // TODO: Add specific method tests\n"
                f"  }});\n"
                f"}}\n"
            )

        result["status"] = "ok"
        return result

    # ── match_view_guards ────────────────────────────────────────────

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match route guard redirects to widget conditional builds and provider auth state checks."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol,
            "route_guards": [],
            "widget_guards": [],
            "provider_guards": [],
            "unmatched": [],
        }

        rel_path = os.path.relpath(full_path, base) if base else file_path

        # Check for route guard redirects in router configuration
        # GoRouter redirect pattern
        redirect_matches = re.findall(
            r"redirect\s*:\s*\([^)]*\)\s*(?:=>)?\s*\{?(.*?)(?:\}|,\s*(?:routes|path|name|builder))",
            content, re.DOTALL,
        )
        for rm in redirect_matches:
            auth_checks = re.findall(
                r"(?:isAuth|isLoggedIn|token|currentUser|authState|isSignedIn|user)\s*(?:==|!=|\?\?|\.)",
                rm, re.IGNORECASE,
            )
            for ac in auth_checks:
                result["route_guards"].append({
                    "type": "route_redirect",
                    "check": ac.strip()[:60],
                    "file": rel_path,
                })

        # AutoRoute guard pattern
        auto_route_guards = re.findall(
            r"class\s+(\w*(?:Auth|Guard)\w*)\s+extends\s+AutoRouteGuard\s*\{(.*?)(?=\nclass\s|\Z)",
            content, re.DOTALL,
        )
        for guard_name, guard_body in auto_route_guards:
            result["route_guards"].append({
                "type": "auto_route_guard",
                "class": guard_name,
                "file": rel_path,
            })

        # Navigator guard pattern
        nav_guards = re.findall(
            r"(?:onGenerateRoute|MaterialPageRoute|pushNamed|pushReplacement).*?(?:isAuth|isLoggedIn|token|currentUser)",
            content, re.IGNORECASE | re.DOTALL,
        )
        for ng in nav_guards:
            result["route_guards"].append({
                "type": "navigator_guard",
                "detail": ng.strip()[:60],
                "file": rel_path,
            })

        # Scan widget files for conditional builds based on auth
        dart_files = self._collect_dart_files(base, ["lib"])

        for fpath, drel in dart_files:
            dart_content = self._read_file(fpath)
            if not dart_content:
                continue

            # Look for conditional widget builds based on auth
            auth_if_builds = re.findall(
                r"(?:if\s*\(\s*|(?:case|when)\s+)(?:[\w.]*(?:isAuth|isLoggedIn|token|currentUser|authState|isSignedIn|user|role|permission)[\w.]*)\s*(?:[!=]==?|\.|\?\.)",
                dart_content, re.IGNORECASE,
            )
            for ab in auth_if_builds:
                result["widget_guards"].append({
                    "file": drel,
                    "condition": ab.strip()[:80],
                    "type": "conditional_build",
                })

            # Look for ternary auth checks in build methods
            ternary_auth = re.findall(
                r"(?:[\w.]*(?:isAuth|isLoggedIn|currentUser|authState|isSignedIn)[\w.]*)\s*\?\s*\w+",
                dart_content, re.IGNORECASE,
            )
            for ta in ternary_auth:
                result["widget_guards"].append({
                    "file": drel,
                    "condition": ta.strip()[:80],
                    "type": "ternary_auth_check",
                })

            # Check for provider/BLoC auth state consumption
            provider_auth = re.findall(
                r"(?:context\.(?:read|watch|select)|ref\.(?:read|watch)|BlocBuilder|BlocListener)\s*[<(]\s*(\w*(?:Auth|Login|Session)\w*)",
                dart_content, re.IGNORECASE,
            )
            for pa in provider_auth:
                result["provider_guards"].append({
                    "file": drel,
                    "provider": pa,
                    "type": "provider_auth_consumption",
                })

        # Identify unmatched guards
        has_route = bool(result["route_guards"])
        has_widget = bool(result["widget_guards"]) or bool(result["provider_guards"])

        if has_route and not has_widget:
            result["unmatched"].append({
                "severity": "warning",
                "message": "Route guards exist but no matching conditional widget builds found -- consider showing/hiding UI based on auth state",
            })
        if has_widget and not has_route:
            result["unmatched"].append({
                "severity": "error",
                "message": "Widget auth conditionals exist but no route guard found -- unauthorized users may access protected screens",
            })

        result["status"] = "ok"
        return result
