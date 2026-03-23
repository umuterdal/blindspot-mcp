"""
Next.js / React / TypeScript Intelligence Service.

Provides cross-file analysis for Next.js projects:
- React component tree and dependency mapping
- App Router and Pages Router API route parsing
- Prisma schema extraction
- State management discovery (Zustand, Redux, Context)
- Middleware chain resolution
- Data-fetching pattern detection
- Validation chain tracing (Zod/Yup)
- Project convention extraction
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class NextjsIntelligenceService(BaseService):
    """Next.js / React / TypeScript code intelligence."""

    # Common React hook prefixes
    REACT_HOOKS = [
        "useState", "useEffect", "useContext", "useReducer", "useCallback",
        "useMemo", "useRef", "useLayoutEffect", "useImperativeHandle",
        "useDebugValue", "useDeferredValue", "useTransition", "useId",
        "useSyncExternalStore", "useInsertionEffect", "useOptimistic",
        "useFormStatus", "useFormState", "useActionState",
    ]

    # Next.js specific hooks
    NEXTJS_HOOKS = [
        "useRouter", "usePathname", "useSearchParams", "useParams",
        "useSelectedLayoutSegment", "useSelectedLayoutSegments",
        "useReportWebVitals",
    ]

    # Data-fetching library hooks
    DATA_HOOKS = [
        "useQuery", "useMutation", "useInfiniteQuery", "useSuspenseQuery",
        "useSWR", "useSWRInfinite", "useSWRMutation",
    ]

    # Pattern registry for get_similar_patterns
    PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
        "modal": {
            "keywords": ["modal", "dialog", "overlay", "backdrop", "popup"],
            "file_patterns": [r"modal", r"dialog", r"popup", r"overlay"],
            "code_patterns": [
                r"(?:is|show|open)\s*(?:Modal|Dialog|Popup)",
                r"onClose|onDismiss|handleClose",
                r"(?:Dialog|Modal)\.(?:Root|Content|Overlay)",
                r"createPortal",
            ],
        },
        "dropdown": {
            "keywords": ["dropdown", "select", "menu", "popover", "combobox"],
            "file_patterns": [r"dropdown", r"select", r"menu", r"popover", r"combobox"],
            "code_patterns": [
                r"(?:is|show)(?:Open|Dropdown|Menu)",
                r"(?:Dropdown|Select|Menu)\.(?:Root|Trigger|Content|Item)",
                r"onSelect|onChange|handleSelect",
            ],
        },
        "form": {
            "keywords": ["form", "input", "field", "validation", "submit"],
            "file_patterns": [r"form", r"input", r"field"],
            "code_patterns": [
                r"useForm|useFormContext|useFieldArray",
                r"handleSubmit|onSubmit",
                r"register\s*\(|control\s*[,}]",
                r"<form\b",
                r"zodResolver|yupResolver",
            ],
        },
        "table": {
            "keywords": ["table", "datagrid", "data-table", "grid", "list"],
            "file_patterns": [r"table", r"datagrid", r"data.?table", r"grid"],
            "code_patterns": [
                r"<(?:table|Table|DataTable|DataGrid)",
                r"useReactTable|createColumnHelper",
                r"columns?\s*[=:]",
                r"<t(?:head|body|r|d|h)\b",
                r"sortBy|filterBy|pageSize",
            ],
        },
        "pagination": {
            "keywords": ["pagination", "paginate", "pager", "page-size", "cursor"],
            "file_patterns": [r"pagination", r"pagina"],
            "code_patterns": [
                r"(?:current|total)Page|pageSize|pageCount",
                r"(?:next|prev|first|last)Page",
                r"offset|cursor|skip|take|limit",
                r"usePagination",
            ],
        },
        "auth": {
            "keywords": ["auth", "login", "signin", "signup", "session", "jwt", "token"],
            "file_patterns": [r"auth", r"login", r"sign.?in", r"session"],
            "code_patterns": [
                r"useSession|useAuth|useUser",
                r"signIn|signOut|signUp|login|logout",
                r"getServerSession|getSession|getToken",
                r"NextAuth|authOptions|providers",
                r"middleware.*auth",
                r"jwt|accessToken|refreshToken",
            ],
        },
        "upload": {
            "keywords": ["upload", "file", "dropzone", "drag", "attachment"],
            "file_patterns": [r"upload", r"dropzone", r"file.?input"],
            "code_patterns": [
                r"(?:on|handle)(?:Drop|Upload|FileChange)",
                r"(?:type|accept)\s*=\s*['\"]file",
                r"FormData|multipart",
                r"useDropzone",
                r"(?:drag|drop)(?:Enter|Leave|Over|End)",
            ],
        },
        "toast": {
            "keywords": ["toast", "notification", "snackbar", "alert", "banner"],
            "file_patterns": [r"toast", r"notification", r"snackbar", r"alert"],
            "code_patterns": [
                r"toast\s*\(|toast\.\w+\s*\(",
                r"useToast|useNotification",
                r"(?:show|add|dismiss)(?:Toast|Notification)",
                r"Toaster|ToastProvider",
            ],
        },
        "search": {
            "keywords": ["search", "filter", "query", "autocomplete", "typeahead"],
            "file_patterns": [r"search", r"filter", r"autocomplete"],
            "code_patterns": [
                r"(?:search|filter|query)(?:Term|Text|Value|Input)",
                r"useDebounce|debounce",
                r"onSearch|handleSearch",
                r"<(?:Search|Command)(?:Input|Bar|Box)",
            ],
        },
        "infinite-scroll": {
            "keywords": ["infinite", "scroll", "virtualize", "virtual", "windowed"],
            "file_patterns": [r"infinite", r"virtual", r"scroll"],
            "code_patterns": [
                r"useInfiniteQuery|useSWRInfinite|useInfiniteScroll",
                r"IntersectionObserver|useInView",
                r"fetchNextPage|loadMore|hasNextPage",
                r"(?:Virtual|Virtualized|Virtuoso|WindowScroller)",
            ],
        },
        "tabs": {
            "keywords": ["tabs", "tab", "tabbed", "tab-panel"],
            "file_patterns": [r"tabs?(?:\b|[A-Z_])", r"tab.?panel"],
            "code_patterns": [
                r"(?:active|selected)Tab|tabIndex|tabValue",
                r"Tabs?\.(?:Root|List|Trigger|Content|Panel)",
                r"<Tab(?:s|Panel|List|Trigger)\b",
            ],
        },
        "accordion": {
            "keywords": ["accordion", "collapsible", "expandable", "disclosure"],
            "file_patterns": [r"accordion", r"collapsible", r"expandable"],
            "code_patterns": [
                r"(?:is|set)(?:Open|Expanded|Collapsed)",
                r"Accordion\.(?:Root|Item|Trigger|Content)",
                r"useDisclosure|useCollapsible",
            ],
        },
        "sidebar": {
            "keywords": ["sidebar", "drawer", "navigation", "nav", "sidenav"],
            "file_patterns": [r"sidebar", r"drawer", r"side.?nav", r"navigation"],
            "code_patterns": [
                r"(?:is|set)(?:Sidebar|Drawer|Nav)(?:Open|Visible|Collapsed)",
                r"useSidebar|useDrawer",
                r"<(?:Sidebar|Drawer|SideNav)\b",
            ],
        },
        "dialog": {
            "keywords": ["dialog", "confirm", "prompt", "alert-dialog"],
            "file_patterns": [r"dialog", r"confirm", r"alert.?dialog"],
            "code_patterns": [
                r"(?:Alert)?Dialog\.(?:Root|Content|Trigger|Action|Cancel)",
                r"useDialog|useConfirm",
                r"(?:show|open)(?:Dialog|Confirm)",
            ],
        },
    }

    # ── helpers ────────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the Next.js project root path.

        In a monorepo, finds the workspace containing Next.js (looks for
        'next' in package.json dependencies). Falls back to base_path.
        """
        try:
            base = self.base_path
            if not base or not os.path.isdir(base):
                return None

            # Check if base itself is a Next.js project
            if os.path.isfile(os.path.join(base, "next.config.js")) or \
               os.path.isfile(os.path.join(base, "next.config.ts")) or \
               os.path.isfile(os.path.join(base, "next.config.mjs")):
                return base

            # Monorepo: search common workspace directories
            for name in ["frontend", "web", "client", "app", "packages"]:
                sub = os.path.join(base, name)
                for cfg in ["next.config.js", "next.config.ts", "next.config.mjs"]:
                    if os.path.isfile(os.path.join(sub, cfg)):
                        return sub

            return base
        except Exception:
            pass
        return None

    def _find_src_root(self, base: str) -> str:
        """Return 'src' prefix if src/ directory exists, else empty string."""
        if os.path.isdir(os.path.join(base, "src")):
            return "src"
        return ""

    def _walk_files(self, directory: str, extensions: Tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")) -> List[str]:
        """Walk directory and return files matching extensions."""
        results = []
        if not os.path.isdir(directory):
            return results
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.endswith(extensions):
                    results.append(os.path.join(root, fname))
        return results

    def _read_file(self, fpath: str) -> Optional[str]:
        """Read file content safely."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def _extract_imports(self, content: str) -> List[Dict[str, Any]]:
        """Extract import statements from a TS/JS file."""
        imports = []
        # import { Foo, Bar } from 'module'
        pattern = re.compile(
            r"""import\s+(?:"""
            r"""(?:(\w+)\s*,?\s*)?"""  # default import
            r"""(?:\{([^}]*)\}\s*,?\s*)?"""  # named imports
            r"""(?:\*\s+as\s+(\w+)\s*)?"""  # namespace import
            r""")?\s*from\s*['"]([@\w./-]+)['"]""",
            re.MULTILINE,
        )
        for m in pattern.finditer(content):
            default_import = m.group(1)
            named_raw = m.group(2)
            namespace = m.group(3)
            source = m.group(4)

            named = []
            if named_raw:
                for n in re.findall(r"(\w+)(?:\s+as\s+\w+)?", named_raw):
                    named.append(n)

            imports.append({
                "source": source,
                "default": default_import,
                "named": named,
                "namespace": namespace,
            })

        # Dynamic imports: import('module') or require('module')
        dynamic_pattern = re.compile(r"""(?:import|require)\s*\(\s*['"]([@\w./-]+)['"]\s*\)""")
        for m in dynamic_pattern.finditer(content):
            imports.append({
                "source": m.group(1),
                "default": None,
                "named": [],
                "namespace": None,
                "dynamic": True,
            })

        return imports

    def _extract_hooks(self, content: str) -> List[str]:
        """Extract React hook calls from component content."""
        hooks = []
        # Match useXxx( patterns
        hook_pattern = re.compile(r"\b(use[A-Z]\w*)\s*\(")
        for m in hook_pattern.finditer(content):
            hook_name = m.group(1)
            if hook_name not in hooks:
                hooks.append(hook_name)
        return hooks

    def _extract_props_interface(self, content: str, component_name: str = "") -> List[Dict[str, str]]:
        """Extract Props interface/type from a component file."""
        props = []

        # Pattern 1: interface FooProps { ... } or type FooProps = { ... }
        patterns = [
            re.compile(
                r"(?:interface|type)\s+(\w*Props\w*)\s*(?:=\s*)?\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
                re.DOTALL,
            ),
            # Pattern 2: FC<{ prop: type }> inline
            re.compile(
                r"(?:React\.)?FC\s*<\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}\s*>",
                re.DOTALL,
            ),
            # Pattern 3: function Component({ prop1, prop2 }: FooProps)
            re.compile(
                r"function\s+\w+\s*\(\s*\{([^}]*)\}\s*:\s*(\w+)",
            ),
        ]

        for pat in patterns:
            for m in pat.finditer(content):
                group_content = m.group(2) if pat == patterns[0] else m.group(1)
                # Parse individual props
                prop_pattern = re.compile(
                    r"(\w+)\s*(\?)?:\s*([^;\n,]+)"
                )
                for pm in prop_pattern.finditer(group_content):
                    props.append({
                        "name": pm.group(1),
                        "optional": pm.group(2) == "?",
                        "type": pm.group(3).strip().rstrip(",;"),
                    })

        # Pattern 4: destructured props in function args without type annotation
        if not props:
            destr = re.compile(
                r"(?:export\s+(?:default\s+)?)?function\s+\w+\s*\(\s*\{\s*([^}]+)\}\s*(?::\s*\w+)?\s*\)"
            )
            for m in destr.finditer(content):
                for p in re.findall(r"(\w+)(?:\s*=\s*[^,}]+)?", m.group(1)):
                    if p not in ("children",):
                        props.append({"name": p, "optional": False, "type": "unknown"})
                # Add children if present
                if "children" in m.group(1):
                    props.append({"name": "children", "optional": False, "type": "React.ReactNode"})

        return props

    def _extract_component_name(self, content: str, fpath: str) -> Optional[str]:
        """Extract the primary component name from a file."""
        # Try export default function/const name
        m = re.search(r"export\s+default\s+function\s+(\w+)", content)
        if m:
            return m.group(1)

        m = re.search(r"export\s+default\s+(\w+)", content)
        if m and m.group(1)[0].isupper():
            return m.group(1)

        # Named export with function declaration
        m = re.search(r"export\s+(?:const|function)\s+(\w+)\s*[:=(]", content)
        if m and m.group(1)[0].isupper():
            return m.group(1)

        # Fallback: filename
        fname = os.path.basename(fpath)
        name = re.sub(r"\.(tsx?|jsx?)$", "", fname)
        if name in ("index", "page", "layout", "template", "loading", "error", "not-found"):
            # Use parent directory name
            parent = os.path.basename(os.path.dirname(fpath))
            if parent and parent not in ("src", "app", "components", "pages"):
                return parent.replace("-", "").replace("_", "").title()
            return name
        return name

    def _is_component_file(self, content: str) -> bool:
        """Check if a file likely contains a React component."""
        # Has JSX return
        if re.search(r"return\s*\(?\s*<", content):
            return True
        # Has React.createElement
        if "createElement" in content:
            return True
        # Has export default with JSX
        if re.search(r"export\s+default", content) and "<" in content:
            return True
        return False

    def _is_client_component(self, content: str) -> bool:
        """Check if file has 'use client' directive."""
        return bool(re.match(r"""^['"]use client['"]""", content.strip()))

    def _is_server_component(self, content: str) -> bool:
        """Check if file has 'use server' directive."""
        return bool(re.match(r"""^['"]use server['"]""", content.strip()))

    def _find_child_components(self, content: str) -> List[str]:
        """Find child component references in JSX."""
        children = []
        # <ComponentName or <Namespace.ComponentName
        jsx_pattern = re.compile(r"<([A-Z]\w+(?:\.\w+)?)")
        for m in jsx_pattern.finditer(content):
            name = m.group(1)
            # Filter out HTML-like names and common non-components
            if name not in children and name not in (
                "React", "Fragment", "Suspense", "StrictMode",
            ):
                children.append(name)
        return children

    def _get_component_dirs(self, base: str) -> List[str]:
        """Get directories where components might live.

        Checks both src/ and root-level directories to support all Next.js structures:
        - src/components + app/components
        - src/app + app/
        - pages/
        """
        src = self._find_src_root(base)
        candidates = set()
        # Always check root-level directories
        for d in ["components", "app", "pages", "features", "modules", "views", "ui",
                   "app/components", "app/ui", "sections"]:
            candidates.add(os.path.join(base, d))
        # Also check src/ prefixed directories
        if src:
            for d in ["components", "app", "features", "modules", "views", "ui"]:
                candidates.add(os.path.join(base, src, d))
        return [d for d in candidates if os.path.isdir(d)]

    # ── 1. get_component_tree ─────────────────────────────────────────

    def get_component_tree(self, component_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Scan src/components/, src/app/ for React components.
        Extract: props interface, imports, children components, hooks used.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        component_dirs = self._get_component_dirs(base)
        if not component_dirs:
            return {"status": "error", "message": "No component directories found (checked components/, app/, pages/)"}

        tree: Dict[str, Any] = {}

        for cdir in component_dirs:
            for fpath in self._walk_files(cdir, (".tsx", ".jsx", ".ts", ".js")):
                content = self._read_file(fpath)
                if not content:
                    continue

                if not self._is_component_file(content):
                    continue

                name = self._extract_component_name(content, fpath)
                if not name:
                    continue

                if component_name and name.lower() != component_name.lower():
                    continue

                rel_path = os.path.relpath(fpath, base)
                imports = self._extract_imports(content)
                hooks = self._extract_hooks(content)
                props = self._extract_props_interface(content, name)
                children = self._find_child_components(content)
                is_client = self._is_client_component(content)
                is_server = self._is_server_component(content)

                # Detect exported component type
                has_default_export = bool(re.search(r"export\s+default\b", content))
                has_named_export = bool(re.search(r"export\s+(?:const|function|class)\s+\w+", content))

                # Detect forwardRef
                uses_forward_ref = "forwardRef" in content

                # Detect memo
                uses_memo = bool(re.search(r"\bReact\.memo\b|\bmemo\s*\(", content))

                tree[name] = {
                    "file": rel_path,
                    "props": props,
                    "hooks": hooks,
                    "children_components": children,
                    "imports": [
                        {"source": imp["source"], "names": imp["named"], "default": imp.get("default")}
                        for imp in imports
                        if not imp["source"].startswith(".")  # external only for summary
                    ][:15],
                    "local_imports": [
                        imp["source"]
                        for imp in imports
                        if imp["source"].startswith(".")
                    ],
                    "is_client": is_client,
                    "is_server": is_server,
                    "has_default_export": has_default_export,
                    "has_named_export": has_named_export,
                    "uses_forward_ref": uses_forward_ref,
                    "uses_memo": uses_memo,
                }

        if component_name and not tree:
            return {"status": "error", "message": f"Component '{component_name}' not found"}

        return {
            "status": "ok",
            "components": tree,
            "total": len(tree),
            "scanned_dirs": [os.path.relpath(d, base) for d in component_dirs],
        }

    # ── 2. get_api_routes ─────────────────────────────────────────────

    def get_api_routes(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Next.js App Router (src/app/**/route.ts) and Pages Router (pages/api/**).
        Extract: HTTP methods, middleware, params.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        routes: List[Dict[str, Any]] = []

        # ── App Router: app/**/route.ts|js ───────────────────────────
        # Check both src/app and root-level app
        app_dir = os.path.join(base, "app")
        if src and os.path.isdir(os.path.join(base, src, "app")):
            app_dir = os.path.join(base, src, "app")
        if os.path.isdir(app_dir):
            for fpath in self._walk_files(app_dir):
                fname = os.path.basename(fpath)
                if not re.match(r"route\.(ts|js|tsx|jsx)$", fname):
                    continue

                content = self._read_file(fpath)
                if not content:
                    continue

                rel = os.path.relpath(fpath, app_dir)
                # Convert file path to URL route
                route_path = "/" + os.path.dirname(rel).replace("\\", "/")
                route_path = re.sub(r"\(.*?\)/", "", route_path)  # remove route groups
                route_path = route_path.replace("[", ":").replace("]", "")
                route_path = re.sub(r"/+$", "", route_path) or "/"

                if filter_prefix and not route_path.startswith(filter_prefix):
                    continue

                # Extract exported HTTP methods
                http_methods = []
                method_pattern = re.compile(
                    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(",
                    re.MULTILINE,
                )
                for mm in method_pattern.finditer(content):
                    http_methods.append(mm.group(1))

                # Also check for exported const
                const_method_pattern = re.compile(
                    r"export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*=",
                    re.MULTILINE,
                )
                for mm in const_method_pattern.finditer(content):
                    if mm.group(1) not in http_methods:
                        http_methods.append(mm.group(1))

                # Detect dynamic segments
                segments = re.findall(r"\[([^\]]+)\]", os.path.dirname(rel))
                params = []
                for seg in segments:
                    if seg.startswith("..."):
                        params.append({"name": seg[3:], "type": "catch-all"})
                    elif seg.startswith("[") and seg.endswith("]"):
                        params.append({"name": seg[1:-1], "type": "optional-catch-all"})
                    else:
                        params.append({"name": seg, "type": "dynamic"})

                # Detect auth/middleware patterns in handler
                has_auth = bool(re.search(
                    r"getServerSession|getToken|auth\(\)|nextAuth|getSession|"
                    r"headers\(\).*(?:authorization|bearer)|cookies\(\)",
                    content, re.IGNORECASE,
                ))

                # Detect response types
                uses_next_response = "NextResponse" in content
                uses_json = bool(re.search(r"NextResponse\.json|Response\.json|json\(", content))
                uses_redirect = bool(re.search(r"redirect\(|NextResponse\.redirect", content))

                routes.append({
                    "path": route_path,
                    "file": os.path.relpath(fpath, base),
                    "type": "app-router",
                    "methods": http_methods,
                    "params": params,
                    "has_auth_check": has_auth,
                    "uses_next_response": uses_next_response,
                    "response_types": {
                        "json": uses_json,
                        "redirect": uses_redirect,
                    },
                })

        # ── Pages Router: pages/api/** ───────────────────────────────
        pages_api_dir = os.path.join(base, "pages", "api")
        if os.path.isdir(pages_api_dir):
            for fpath in self._walk_files(pages_api_dir):
                content = self._read_file(fpath)
                if not content:
                    continue

                rel = os.path.relpath(fpath, os.path.join(base, "pages"))
                route_path = "/api/" + re.sub(
                    r"\.(ts|js|tsx|jsx)$", "",
                    os.path.relpath(fpath, pages_api_dir).replace("\\", "/"),
                )
                route_path = route_path.replace("[", ":").replace("]", "")
                route_path = route_path.replace("/index", "")

                if filter_prefix and not route_path.startswith(filter_prefix):
                    continue

                # Pages API uses default export handler
                has_default_export = bool(re.search(r"export\s+default", content))

                # Check for method switching
                method_checks = re.findall(
                    r"""req\.method\s*===?\s*['"](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['"]""",
                    content,
                )
                if not method_checks:
                    method_checks = ["ALL"]

                # Detect dynamic segments
                segments = re.findall(r"\[([^\]]+)\]", rel)
                params = [{"name": s.lstrip("..."), "type": "catch-all" if s.startswith("...") else "dynamic"} for s in segments]

                routes.append({
                    "path": route_path,
                    "file": os.path.relpath(fpath, base),
                    "type": "pages-router",
                    "methods": list(set(method_checks)),
                    "params": params,
                    "has_default_export": has_default_export,
                })

        return {
            "status": "ok",
            "routes": routes,
            "total": len(routes),
            "app_router_count": sum(1 for r in routes if r.get("type") == "app-router"),
            "pages_router_count": sum(1 for r in routes if r.get("type") == "pages-router"),
        }

    # ── 3. get_prisma_schema ──────────────────────────────────────────

    def get_prisma_schema(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse prisma/schema.prisma for models, fields, relations, enums, indexes.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Search for schema file
        schema_path = None
        candidates = [
            os.path.join(base, "prisma", "schema.prisma"),
            os.path.join(base, "schema.prisma"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                schema_path = c
                break

        if not schema_path:
            return {"status": "error", "message": "prisma/schema.prisma not found"}

        content = self._read_file(schema_path)
        if not content:
            return {"status": "error", "message": "Could not read schema file"}

        models: Dict[str, Any] = {}
        enums: Dict[str, List[str]] = {}

        # Extract datasource
        ds_match = re.search(r"datasource\s+\w+\s*\{([^}]+)\}", content)
        datasource = {}
        if ds_match:
            for line in ds_match.group(1).strip().split("\n"):
                kv = re.match(r"\s*(\w+)\s*=\s*(.+)", line.strip())
                if kv:
                    datasource[kv.group(1)] = kv.group(2).strip().strip('"')

        # Extract generator
        gen_match = re.search(r"generator\s+\w+\s*\{([^}]+)\}", content)
        generator = {}
        if gen_match:
            for line in gen_match.group(1).strip().split("\n"):
                kv = re.match(r"\s*(\w+)\s*=\s*(.+)", line.strip())
                if kv:
                    generator[kv.group(1)] = kv.group(2).strip().strip('"')

        # Extract enums
        enum_pattern = re.compile(r"enum\s+(\w+)\s*\{([^}]+)\}", re.DOTALL)
        for m in enum_pattern.finditer(content):
            enum_name = m.group(1)
            values = [v.strip() for v in m.group(2).strip().split("\n") if v.strip() and not v.strip().startswith("//")]
            enums[enum_name] = values

        # Extract models
        model_pattern = re.compile(r"model\s+(\w+)\s*\{([^}]+)\}", re.DOTALL)
        for m in model_pattern.finditer(content):
            mname = m.group(1)
            if model_name and mname.lower() != model_name.lower():
                continue

            body = m.group(2)
            fields = []
            relations = []
            indexes = []
            uniques = []
            model_map = []

            for line in body.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue

                # Block-level attributes: @@index, @@unique, @@map, @@id
                if line.startswith("@@"):
                    attr_match = re.match(r"@@(\w+)\(([^)]*)\)", line)
                    if attr_match:
                        attr_name = attr_match.group(1)
                        attr_args = attr_match.group(2)
                        if attr_name == "index":
                            field_list = re.findall(r"(\w+)", attr_args)
                            indexes.append({"fields": field_list, "raw": line})
                        elif attr_name == "unique":
                            field_list = re.findall(r"(\w+)", attr_args)
                            uniques.append({"fields": field_list, "raw": line})
                        elif attr_name == "map":
                            map_val = re.search(r'["\']([^"\']+)["\']', attr_args)
                            if map_val:
                                model_map.append(map_val.group(1))
                        elif attr_name == "id":
                            field_list = re.findall(r"(\w+)", attr_args)
                            indexes.append({"fields": field_list, "type": "compound_id", "raw": line})
                    continue

                # Field definition: name Type @attribute @attribute
                field_match = re.match(
                    r"(\w+)\s+([\w\[\]?]+)(?:\s+(.*))?$",
                    line,
                )
                if not field_match:
                    continue

                fname = field_match.group(1)
                ftype_raw = field_match.group(2)
                attrs_raw = field_match.group(3) or ""

                is_optional = "?" in ftype_raw
                is_list = "[]" in ftype_raw
                ftype = ftype_raw.replace("?", "").replace("[]", "")

                # Parse field-level attributes
                is_id = "@id" in attrs_raw
                is_unique = "@unique" in attrs_raw
                has_default = "@default" in attrs_raw
                is_updated_at = "@updatedAt" in attrs_raw
                is_map = "@map" in attrs_raw

                default_value = None
                if has_default:
                    dm = re.search(r"@default\(([^)]+)\)", attrs_raw)
                    if dm:
                        default_value = dm.group(1)

                # Check for @relation
                rel_match = re.search(
                    r'@relation\(([^)]*)\)',
                    attrs_raw,
                )
                if rel_match:
                    rel_args = rel_match.group(1)
                    rel_fields = re.findall(r'fields:\s*\[([^\]]*)\]', rel_args)
                    rel_references = re.findall(r'references:\s*\[([^\]]*)\]', rel_args)
                    rel_name_match = re.search(r'^"([^"]*)"', rel_args.strip())

                    relations.append({
                        "field": fname,
                        "type": ftype,
                        "is_list": is_list,
                        "relation_name": rel_name_match.group(1) if rel_name_match else None,
                        "fields": [f.strip() for f in rel_fields[0].split(",")] if rel_fields else [],
                        "references": [f.strip() for f in rel_references[0].split(",")] if rel_references else [],
                        "on_delete": self._extract_prisma_attr(attrs_raw, "onDelete"),
                        "on_update": self._extract_prisma_attr(attrs_raw, "onUpdate"),
                    })
                elif ftype[0].isupper() and ftype not in enums:
                    # Implicit relation (type is another model)
                    relations.append({
                        "field": fname,
                        "type": ftype,
                        "is_list": is_list,
                        "relation_name": None,
                        "fields": [],
                        "references": [],
                    })
                    continue

                fields.append({
                    "name": fname,
                    "type": ftype,
                    "is_optional": is_optional,
                    "is_list": is_list,
                    "is_id": is_id,
                    "is_unique": is_unique,
                    "default": default_value,
                    "is_updated_at": is_updated_at,
                })

            models[mname] = {
                "fields": fields,
                "relations": relations,
                "indexes": indexes,
                "unique_constraints": uniques,
                "map": model_map,
            }

        if model_name and not models:
            return {"status": "error", "message": f"Model '{model_name}' not found in schema"}

        return {
            "status": "ok",
            "models": models,
            "enums": enums,
            "datasource": datasource,
            "generator": generator,
            "total_models": len(models),
            "total_enums": len(enums),
        }

    def _extract_prisma_attr(self, attrs: str, name: str) -> Optional[str]:
        """Extract a named attribute value from Prisma field attributes."""
        m = re.search(rf"{name}:\s*(\w+)", attrs)
        return m.group(1) if m else None

    # ── 4. get_component_dependencies ─────────────────────────────────

    def get_component_dependencies(self, component_path: str) -> Dict[str, Any]:
        """
        For a component file, trace:
        - parent components importing it
        - child components it imports
        - props interface
        - hooks
        - context providers/consumers
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, component_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {component_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        component_name = self._extract_component_name(content, full_path)
        imports = self._extract_imports(content)
        hooks = self._extract_hooks(content)
        props = self._extract_props_interface(content, component_name or "")
        children = self._find_child_components(content)
        is_client = self._is_client_component(content)

        # Identify child component imports (local files that are components)
        child_imports = []
        for imp in imports:
            if imp["source"].startswith("."):
                default_name = imp.get("default")
                named_names = imp.get("named", [])
                all_names = ([default_name] if default_name else []) + named_names
                for n in all_names:
                    if n and n[0].isupper():
                        child_imports.append({
                            "name": n,
                            "source": imp["source"],
                        })

        # Detect context usage
        context_consumers = []
        context_providers = []

        # useContext(SomeContext)
        ctx_pattern = re.compile(r"useContext\(\s*(\w+)\s*\)")
        for m in ctx_pattern.finditer(content):
            context_consumers.append(m.group(1))

        # Custom context hooks: useSomeContext()
        custom_ctx = re.compile(r"\buse(\w*Context)\s*\(")
        for m in custom_ctx.finditer(content):
            context_consumers.append(m.group(1))

        # <SomeContext.Provider or <SomeProvider
        provider_pattern = re.compile(r"<(\w+)(?:\.Provider|\s)")
        for m in provider_pattern.finditer(content):
            name = m.group(1)
            if "Provider" in name or "Context" in name:
                context_providers.append(name)

        # Find parent components that import this file
        parents = []
        fname_no_ext = re.sub(r"\.(tsx?|jsx?)$", "", os.path.basename(full_path))
        component_dirs = self._get_component_dirs(base)

        # Also check app/ directory for parent pages/layouts
        src = self._find_src_root(base)
        app_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
        search_dirs = list(set(component_dirs + ([app_dir] if os.path.isdir(app_dir) else [])))

        for sdir in search_dirs:
            for fpath in self._walk_files(sdir):
                if fpath == full_path:
                    continue
                pcontent = self._read_file(fpath)
                if not pcontent:
                    continue

                # Check if this file imports our component
                if component_name and re.search(
                    rf"""(?:import\s+.*\b{re.escape(component_name)}\b.*from|"""
                    rf"""import\s+{re.escape(component_name)}\s+from)""",
                    pcontent,
                ):
                    parents.append({
                        "file": os.path.relpath(fpath, base),
                        "component": self._extract_component_name(pcontent, fpath),
                    })
                # Also check import path
                elif re.search(re.escape(fname_no_ext) + r"""['"]""", pcontent):
                    parents.append({
                        "file": os.path.relpath(fpath, base),
                        "component": self._extract_component_name(pcontent, fpath),
                    })

        return {
            "status": "ok",
            "component": component_name,
            "file": component_path,
            "is_client_component": is_client,
            "props": props,
            "hooks": hooks,
            "children": child_imports,
            "children_jsx": children,
            "context_consumers": context_consumers,
            "context_providers": context_providers,
            "parents": parents[:20],
            "parent_count": len(parents),
            "imports_external": [
                imp["source"] for imp in imports if not imp["source"].startswith(".")
            ],
        }

    # ── 5. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace:
        - API route -> middleware.ts -> handler -> service call -> Prisma query -> response
        - page.tsx -> server action -> API -> DB
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        flows: List[Dict[str, Any]] = []

        # Find the entry point file
        entry_file = None
        entry_type = None

        # Check if entry_point is a file path
        candidate = os.path.join(base, entry_point)
        if os.path.isfile(candidate):
            entry_file = candidate
            if "route." in os.path.basename(candidate):
                entry_type = "api-route"
            elif "page." in os.path.basename(candidate):
                entry_type = "page"
            elif "action" in os.path.basename(candidate).lower():
                entry_type = "server-action"
            else:
                entry_type = "file"
        else:
            # Search for matching route or page
            app_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
            if os.path.isdir(app_dir):
                for fpath in self._walk_files(app_dir):
                    rel = os.path.relpath(fpath, app_dir)
                    route_path = "/" + os.path.dirname(rel).replace("\\", "/")
                    route_path = re.sub(r"\(.*?\)/", "", route_path)
                    route_path = re.sub(r"/+$", "", route_path) or "/"

                    if entry_point.startswith("/") and route_path == entry_point:
                        fname = os.path.basename(fpath)
                        if "route." in fname:
                            entry_file = fpath
                            entry_type = "api-route"
                            break
                        elif "page." in fname:
                            entry_file = fpath
                            entry_type = "page"
                            break

        if not entry_file:
            return {"status": "error", "message": f"Entry point not found: {entry_point}"}

        content = self._read_file(entry_file)
        if not content:
            return {"status": "error", "message": "Could not read entry point file"}

        rel_entry = os.path.relpath(entry_file, base)
        flow: Dict[str, Any] = {
            "entry": rel_entry,
            "type": entry_type,
            "steps": [],
        }

        # Step 1: Check middleware
        middleware_file = None
        for mf in [
            os.path.join(base, src, "middleware.ts") if src else os.path.join(base, "middleware.ts"),
            os.path.join(base, src, "middleware.js") if src else os.path.join(base, "middleware.js"),
        ]:
            if os.path.isfile(mf):
                middleware_file = mf
                break

        if middleware_file:
            mw_content = self._read_file(middleware_file)
            if mw_content:
                # Check if middleware matches this route
                matcher = re.search(r"config\s*=\s*\{[^}]*matcher\s*:\s*(\[.*?\]|['\"][^'\"]+['\"])", mw_content, re.DOTALL)
                flow["steps"].append({
                    "step": "middleware",
                    "file": os.path.relpath(middleware_file, base),
                    "matcher": matcher.group(1) if matcher else "all routes",
                })

        # Step 2: Parse handler body
        if entry_type == "api-route":
            # Find exported methods
            method_pattern = re.compile(
                r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\s*\("
                r"([\s\S]*?)(?=export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|PATCH|DELETE)|$)",
                re.MULTILINE,
            )
            for mm in method_pattern.finditer(content):
                http_method = mm.group(1)
                if method and method.upper() != http_method:
                    continue

                body = mm.group(2)
                handler_flow = self._trace_handler_body(body, base, src)
                flow["steps"].append({
                    "step": "handler",
                    "method": http_method,
                    **handler_flow,
                })

        elif entry_type == "page":
            # Check for server actions
            server_actions = re.findall(
                r"""['"]use server['"]\s*;?\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)""",
                content,
            )

            # Check for data fetching
            fetch_calls = re.findall(r"(?:await\s+)?fetch\s*\(\s*['\"`]([^'\"`]+)['\"`]", content)
            api_calls = re.findall(r"(?:await\s+)?(\w+(?:\.\w+)*)\s*\(", content)

            # Check for Prisma usage
            prisma_calls = re.findall(r"prisma\.(\w+)\.(\w+)\s*\(", content)

            flow["steps"].append({
                "step": "page-render",
                "is_client": self._is_client_component(content),
                "server_actions": server_actions,
                "fetch_calls": fetch_calls[:10],
                "prisma_calls": [{"model": p[0], "method": p[1]} for p in prisma_calls],
            })

            # Trace server actions
            for action in server_actions:
                action_body_match = re.search(
                    rf"(?:async\s+)?function\s+{re.escape(action)}\s*\([^)]*\)\s*\{{([\s\S]*?)(?=\n\s*(?:export|async\s+function|function)\s|\Z)",
                    content,
                )
                if action_body_match:
                    body = action_body_match.group(1)
                    handler_flow = self._trace_handler_body(body, base, src)
                    flow["steps"].append({
                        "step": "server-action",
                        "name": action,
                        **handler_flow,
                    })

        flows.append(flow)

        return {
            "status": "ok",
            "flows": flows,
        }

    def _trace_handler_body(self, body: str, base: str, src: str) -> Dict[str, Any]:
        """Trace a handler body for service calls, DB queries, etc."""
        result: Dict[str, Any] = {}

        # Prisma operations
        prisma_calls = re.findall(r"prisma\.(\w+)\.(\w+)\s*\(", body)
        if prisma_calls:
            result["prisma_operations"] = [{"model": p[0], "method": p[1]} for p in prisma_calls]

        # Fetch calls
        fetch_calls = re.findall(r"(?:await\s+)?fetch\s*\(\s*['\"`]([^'\"`]+)['\"`]", body)
        if fetch_calls:
            result["fetch_calls"] = fetch_calls[:10]

        # Auth checks
        auth_checks = []
        if re.search(r"getServerSession|auth\(\)|getSession|getToken", body):
            auth_checks.append("session-check")
        if re.search(r"headers\(\).*(?:authorization|bearer)", body, re.IGNORECASE):
            auth_checks.append("header-auth")
        if re.search(r"cookies\(\)", body):
            auth_checks.append("cookie-check")
        if auth_checks:
            result["auth_checks"] = auth_checks

        # Validation
        validation = []
        zod_match = re.findall(r"(\w+)\.(?:parse|safeParse|parseAsync)\s*\(", body)
        if zod_match:
            validation.extend([{"type": "zod", "schema": s} for s in zod_match])
        yup_match = re.findall(r"(\w+)\.(?:validate|validateSync)\s*\(", body)
        if yup_match:
            validation.extend([{"type": "yup", "schema": s} for s in yup_match])
        if validation:
            result["validation"] = validation

        # Response patterns
        responses = []
        if re.search(r"NextResponse\.json\(|Response\.json\(", body):
            responses.append("json")
        if re.search(r"redirect\(|NextResponse\.redirect\(", body):
            responses.append("redirect")
        if re.search(r"NextResponse\.next\(", body):
            responses.append("next")
        if re.search(r"new\s+Response\(", body):
            responses.append("raw-response")
        if responses:
            result["response_types"] = responses

        # Error handling
        has_try_catch = bool(re.search(r"\btry\s*\{", body))
        result["has_error_handling"] = has_try_catch

        # Service/util function calls
        service_calls = re.findall(r"(?:await\s+)?(\w+Service|\w+Util|\w+Helper)\.(\w+)\s*\(", body)
        if service_calls:
            result["service_calls"] = [{"service": s[0], "method": s[1]} for s in service_calls]

        # Email/notification
        if re.search(r"sendEmail|send[Mm]ail|resend\.|nodemailer", body):
            result["side_effects"] = result.get("side_effects", []) + ["email"]
        if re.search(r"queue|bull|agenda|pgBoss", body, re.IGNORECASE):
            result["side_effects"] = result.get("side_effects", []) + ["queue"]

        return result

    # ── 6. get_state_management ───────────────────────────────────────

    def get_state_management(self) -> Dict[str, Any]:
        """
        Find Zustand stores, Redux slices, React Context, Jotai atoms, Recoil atoms.
        Map store -> consumer relationships.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        stores: Dict[str, List[Dict[str, Any]]] = {
            "zustand": [],
            "redux": [],
            "context": [],
            "jotai": [],
            "recoil": [],
        }

        # Directories to scan
        # Scan both src/ and root-level app/ for stores
        scan_dirs = set()
        scan_dirs.add(os.path.join(base, src) if src else base)
        scan_dirs.add(os.path.join(base, "app"))
        if os.path.isdir(os.path.join(base, "app", "store")):
            scan_dirs.add(os.path.join(base, "app", "store"))

        all_files = []
        seen = set()
        for sdir in scan_dirs:
            for f in self._walk_files(sdir):
                if f not in seen:
                    seen.add(f)
                    all_files.append(f)

        # Pass 1: Find store definitions
        for fpath in all_files:
            content = self._read_file(fpath)
            if not content:
                continue

            rel = os.path.relpath(fpath, base)

            # Zustand: create((set, get) => ({ ... }))
            zustand_matches = re.finditer(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*create\s*(?:<[^>]*>)?\s*\(",
                content,
            )
            for m in zustand_matches:
                store_name = m.group(1)
                # Extract state shape
                state_props = re.findall(
                    r"(\w+)\s*:\s*(?:[^,}]+)",
                    content[m.end():m.end() + 500],
                )
                actions = re.findall(
                    r"(\w+)\s*:\s*\([^)]*\)\s*=>",
                    content[m.end():m.end() + 1000],
                )
                stores["zustand"].append({
                    "name": store_name,
                    "file": rel,
                    "state_properties": state_props[:15],
                    "actions": actions[:15],
                })

            # Redux: createSlice({ name: ..., initialState: ..., reducers: ... })
            redux_matches = re.finditer(
                r"createSlice\s*\(\s*\{",
                content,
            )
            for m in redux_matches:
                # Extract slice name
                name_match = re.search(r"name\s*:\s*['\"](\w+)['\"]", content[m.start():m.start() + 500])
                slice_name = name_match.group(1) if name_match else "unknown"

                # Extract reducers
                reducer_section = content[m.start():m.start() + 2000]
                reducers = re.findall(r"(\w+)\s*(?::\s*\(state|:\s*\{)", reducer_section)
                reducers = [r for r in reducers if r not in ("name", "initialState", "reducers", "extraReducers")]

                stores["redux"].append({
                    "name": slice_name,
                    "file": rel,
                    "reducers": reducers[:20],
                })

            # React Context: createContext
            ctx_matches = re.finditer(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?createContext",
                content,
            )
            for m in ctx_matches:
                ctx_name = m.group(1)

                # Find associated provider
                provider_match = re.search(
                    rf"(?:export\s+)?(?:const|function)\s+(\w*Provider\w*)",
                    content,
                )

                # Find custom hook for this context
                hook_match = re.search(
                    rf"(?:export\s+)?(?:const|function)\s+(use\w*)\s*=?\s*\(?\)?\s*(?:=>|{{)\s*(?:return\s+)?useContext\(\s*{re.escape(ctx_name)}\s*\)",
                    content,
                )

                stores["context"].append({
                    "name": ctx_name,
                    "file": rel,
                    "provider": provider_match.group(1) if provider_match else None,
                    "custom_hook": hook_match.group(1) if hook_match else None,
                })

            # Jotai: atom()
            jotai_matches = re.finditer(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*atom\s*(?:<[^>]*>)?\s*\(",
                content,
            )
            for m in jotai_matches:
                stores["jotai"].append({
                    "name": m.group(1),
                    "file": rel,
                })

            # Recoil: atom({ key: ..., default: ... })
            recoil_matches = re.finditer(
                r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*atom\s*\(\s*\{",
                content,
            )
            for m in recoil_matches:
                key_match = re.search(r"key\s*:\s*['\"](\w+)['\"]", content[m.start():m.start() + 300])
                stores["recoil"].append({
                    "name": m.group(1),
                    "file": rel,
                    "key": key_match.group(1) if key_match else None,
                })

        # Pass 2: Find consumers (which files import/use each store)
        store_consumers: Dict[str, List[str]] = {}
        all_store_names: Set[str] = set()

        for stype, slist in stores.items():
            for s in slist:
                name = s["name"]
                all_store_names.add(name)
                store_consumers[name] = []

        if all_store_names:
            for fpath in all_files:
                content = self._read_file(fpath)
                if not content:
                    continue
                rel = os.path.relpath(fpath, base)
                for sname in all_store_names:
                    if re.search(rf"\b{re.escape(sname)}\b", content):
                        if rel not in store_consumers.get(sname, []):
                            store_consumers.setdefault(sname, []).append(rel)

        # Remove empty categories
        stores = {k: v for k, v in stores.items() if v}

        return {
            "status": "ok",
            "stores": stores,
            "consumers": {k: v for k, v in store_consumers.items() if len(v) > 1},
            "summary": {k: len(v) for k, v in stores.items()},
        }

    # ── 7. get_middleware_chain ────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse middleware.ts, find matcher config, extract middleware logic per route.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)

        # Find middleware file
        middleware_file = None
        for mf_path in [
            os.path.join(base, src, "middleware.ts") if src else os.path.join(base, "middleware.ts"),
            os.path.join(base, src, "middleware.js") if src else os.path.join(base, "middleware.js"),
            os.path.join(base, "middleware.ts"),
            os.path.join(base, "middleware.js"),
        ]:
            if os.path.isfile(mf_path):
                middleware_file = mf_path
                break

        if not middleware_file:
            return {"status": "ok", "message": "No middleware.ts found", "middleware": None, "matchers": []}

        content = self._read_file(middleware_file)
        if not content:
            return {"status": "error", "message": "Could not read middleware file"}

        result: Dict[str, Any] = {
            "file": os.path.relpath(middleware_file, base),
            "matchers": [],
            "logic": [],
            "imports": [],
        }

        # Extract matcher config
        # config = { matcher: ['/api/:path*', '/dashboard/:path*'] }
        matcher_match = re.search(
            r"(?:export\s+)?(?:const\s+)?config\s*=\s*\{([^}]*matcher\s*:[^}]*)\}",
            content,
            re.DOTALL,
        )
        if matcher_match:
            config_body = matcher_match.group(1)
            # Array matcher
            array_match = re.search(r"matcher\s*:\s*\[(.*?)\]", config_body, re.DOTALL)
            if array_match:
                matchers = re.findall(r"""['"](.*?)['"]""", array_match.group(1))
                result["matchers"] = matchers
            else:
                # Single string matcher
                single_match = re.search(r"""matcher\s*:\s*['"](.*?)['"]""", config_body)
                if single_match:
                    result["matchers"] = [single_match.group(1)]

        # Check if route_path matches any matcher
        if route_path and result["matchers"]:
            matches_route = False
            for matcher in result["matchers"]:
                # Convert Next.js matcher to rough regex
                regex_matcher = matcher.replace(":path*", ".*").replace(":path", "[^/]+")
                if re.match(regex_matcher, route_path):
                    matches_route = True
                    break
            result["matches_route"] = matches_route

        # Extract middleware logic
        imports = self._extract_imports(content)
        result["imports"] = [imp["source"] for imp in imports]

        # Detect auth checks
        if re.search(r"getToken|getServerSession|auth\(\)|nextAuth|withAuth", content, re.IGNORECASE):
            result["logic"].append("auth-check")

        # Detect redirects
        redirect_patterns = re.findall(
            r"NextResponse\.redirect\(.*?(?:url|URL)\s*\(\s*['\"]([^'\"]*)['\"]",
            content,
        )
        if redirect_patterns:
            result["logic"].append({"type": "redirect", "targets": redirect_patterns})
        elif "redirect" in content.lower():
            result["logic"].append("redirect")

        # Detect rewrites
        if re.search(r"NextResponse\.rewrite", content):
            result["logic"].append("rewrite")

        # Detect header modification
        if re.search(r"(?:request|response)\.headers\.set", content):
            result["logic"].append("header-modification")

        # Detect rate limiting
        if re.search(r"rate.?limit|rateLimit|limiter", content, re.IGNORECASE):
            result["logic"].append("rate-limiting")

        # Detect CORS
        if re.search(r"access.control|cors|CORS", content, re.IGNORECASE):
            result["logic"].append("cors")

        # Detect i18n/locale
        if re.search(r"locale|i18n|language|accept.language", content, re.IGNORECASE):
            result["logic"].append("i18n")

        # Detect pathname checks
        pathname_checks = re.findall(
            r"""(?:pathname|path|url)\.(?:startsWith|includes|match)\s*\(\s*['"](/[^'"]*)['"]\s*\)""",
            content,
        )
        if pathname_checks:
            result["logic"].append({"type": "pathname-checks", "paths": pathname_checks})

        # Conditional blocks
        condition_blocks = re.findall(
            r"if\s*\(\s*(.{10,80}?)\s*\)\s*\{",
            content,
        )
        result["conditions"] = [c.strip() for c in condition_blocks[:10]]

        return {
            "status": "ok",
            "middleware": result,
        }

    # ── 8. get_data_fetching ──────────────────────────────────────────

    def get_data_fetching(self, file_path: str) -> Dict[str, Any]:
        """
        Detect data fetching patterns in a file:
        - Server Component vs Client ('use client')
        - getServerSideProps, getStaticProps
        - fetch() calls
        - React Query/SWR hooks
        - loading.tsx/error.tsx boundaries
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        result: Dict[str, Any] = {
            "file": file_path,
            "component_type": "server",  # default for App Router
            "data_patterns": [],
            "boundaries": {},
        }

        # Determine component type
        is_client = self._is_client_component(content)
        is_server = self._is_server_component(content)
        if is_client:
            result["component_type"] = "client"
        elif is_server:
            result["component_type"] = "server-action"

        # Pages Router data fetching
        if re.search(r"export\s+(?:async\s+)?function\s+getServerSideProps", content):
            match = re.search(
                r"export\s+(?:async\s+)?function\s+getServerSideProps\s*\([^)]*\)\s*\{([\s\S]*?)(?=\nexport|\n\w+\s+function\s|\Z)",
                content,
            )
            body = match.group(1) if match else ""
            result["data_patterns"].append({
                "type": "getServerSideProps",
                "is_async": "async" in (match.group(0) if match else ""),
                "has_fetch": bool(re.search(r"\bfetch\s*\(", body)),
                "has_prisma": bool(re.search(r"prisma\.\w+\.\w+", body)),
                "revalidate": None,
            })

        if re.search(r"export\s+(?:async\s+)?function\s+getStaticProps", content):
            match = re.search(
                r"export\s+(?:async\s+)?function\s+getStaticProps\s*\([^)]*\)\s*\{([\s\S]*?)(?=\nexport|\n\w+\s+function\s|\Z)",
                content,
            )
            body = match.group(1) if match else ""
            revalidate_match = re.search(r"revalidate\s*:\s*(\d+)", body)
            result["data_patterns"].append({
                "type": "getStaticProps",
                "has_fetch": bool(re.search(r"\bfetch\s*\(", body)),
                "has_prisma": bool(re.search(r"prisma\.\w+\.\w+", body)),
                "revalidate": int(revalidate_match.group(1)) if revalidate_match else None,
            })

        if re.search(r"export\s+(?:async\s+)?function\s+getStaticPaths", content):
            result["data_patterns"].append({"type": "getStaticPaths"})

        # App Router: fetch() with Next.js options
        fetch_calls = re.finditer(
            r"""fetch\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*(?:,\s*\{([^}]*)\})?\s*\)""",
            content,
        )
        for fm in fetch_calls:
            url = fm.group(1)
            options = fm.group(2) or ""
            cache_match = re.search(r"""cache\s*:\s*['"]([\w-]+)['"]""", options)
            revalidate_match = re.search(r"(?:next\s*:\s*\{[^}]*)?revalidate\s*:\s*(\d+)", options)
            result["data_patterns"].append({
                "type": "fetch",
                "url": url,
                "cache": cache_match.group(1) if cache_match else "default",
                "revalidate": int(revalidate_match.group(1)) if revalidate_match else None,
            })

        # React Query: useQuery, useSuspenseQuery, useInfiniteQuery
        rq_pattern = re.compile(r"\b(useQuery|useSuspenseQuery|useInfiniteQuery|useMutation)\s*\(")
        for m in rq_pattern.finditer(content):
            hook_name = m.group(1)
            # Try to extract query key
            after = content[m.end():m.end() + 300]
            key_match = re.search(r"""queryKey\s*:\s*\[([^\]]*)\]""", after)
            fn_match = re.search(r"""queryFn\s*:\s*(?:async\s*)?\(\)\s*=>\s*(?:await\s+)?(\w+(?:\.\w+)*)\s*\(""", after)
            result["data_patterns"].append({
                "type": "react-query",
                "hook": hook_name,
                "query_key": key_match.group(1).strip() if key_match else None,
                "query_fn": fn_match.group(1) if fn_match else None,
            })

        # SWR: useSWR, useSWRInfinite
        swr_pattern = re.compile(r"\b(useSWR|useSWRInfinite|useSWRMutation)\s*\(")
        for m in swr_pattern.finditer(content):
            hook_name = m.group(1)
            after = content[m.end():m.end() + 200]
            key_match = re.search(r"""['\"`]([^'\"`]+)['\"`]""", after)
            result["data_patterns"].append({
                "type": "swr",
                "hook": hook_name,
                "key": key_match.group(1) if key_match else None,
            })

        # Server actions
        server_actions = re.findall(
            r"""['"]use server['"]\s*;?\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)""",
            content,
        )
        if server_actions:
            result["data_patterns"].append({
                "type": "server-actions",
                "actions": server_actions,
            })

        # Prisma direct queries
        prisma_calls = re.findall(r"prisma\.(\w+)\.(\w+)\s*\(", content)
        if prisma_calls:
            result["data_patterns"].append({
                "type": "prisma-direct",
                "queries": [{"model": p[0], "method": p[1]} for p in prisma_calls],
            })

        # Check for loading/error boundaries (sibling files)
        fdir = os.path.dirname(full_path)
        for boundary_name in ["loading", "error", "not-found", "template", "layout"]:
            for ext in [".tsx", ".jsx", ".ts", ".js"]:
                boundary_path = os.path.join(fdir, f"{boundary_name}{ext}")
                if os.path.isfile(boundary_path):
                    result["boundaries"][boundary_name] = os.path.relpath(boundary_path, base)
                    break

        # Detect Suspense boundaries in JSX
        suspense_count = len(re.findall(r"<Suspense\b", content))
        if suspense_count:
            result["suspense_boundaries"] = suspense_count

        return {
            "status": "ok",
            **result,
        }

    # ── 9. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check: API route exists, handler exports the HTTP method,
        middleware matches, auth checks present.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        method = method.upper()
        checks: List[Dict[str, Any]] = []

        # Step 1: Find matching route file
        route_file = None
        route_type = None

        # App Router: convert URL to file path
        # /api/users/123 -> app/api/users/[id]/route.ts
        app_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
        if os.path.isdir(app_dir):
            for fpath in self._walk_files(app_dir):
                fname = os.path.basename(fpath)
                if not re.match(r"route\.(ts|js|tsx|jsx)$", fname):
                    continue

                rel = os.path.relpath(fpath, app_dir)
                route_pattern = "/" + os.path.dirname(rel).replace("\\", "/")
                route_pattern = re.sub(r"\(.*?\)/", "", route_pattern)

                # Build regex from route pattern to match URL
                regex = re.sub(r"\[\.\.\.(\w+)\]", r"(?P<\1>.+)", route_pattern)
                regex = re.sub(r"\[(\w+)\]", r"(?P<\1>[^/]+)", regex)
                regex = re.sub(r"/+$", "", regex) or "/"

                if re.match(f"^{regex}$", url):
                    route_file = fpath
                    route_type = "app-router"
                    break

        # Pages Router: pages/api/**
        if not route_file:
            pages_api_dir = os.path.join(base, "pages", "api")
            if os.path.isdir(pages_api_dir):
                for fpath in self._walk_files(pages_api_dir):
                    rel = os.path.relpath(fpath, os.path.join(base, "pages"))
                    route_path = "/" + re.sub(r"\.(ts|js|tsx|jsx)$", "", rel.replace("\\", "/"))
                    route_path = route_path.replace("[", ":").replace("]", "")
                    route_path = route_path.replace("/index", "")

                    # Simple match
                    if url == route_path or url.rstrip("/") == route_path.rstrip("/"):
                        route_file = fpath
                        route_type = "pages-router"
                        break

        # Check 1: Route exists?
        if route_file:
            checks.append({
                "check": "route_exists",
                "passed": True,
                "file": os.path.relpath(route_file, base),
                "type": route_type,
            })
        else:
            checks.append({
                "check": "route_exists",
                "passed": False,
                "message": f"No route file found for {url}",
            })
            return {
                "status": "ok",
                "method": method,
                "url": url,
                "checks": checks,
                "overall": "fail",
            }

        content = self._read_file(route_file)
        if not content:
            return {"status": "error", "message": "Could not read route file"}

        # Check 2: HTTP method exported?
        if route_type == "app-router":
            has_method = bool(re.search(
                rf"export\s+(?:async\s+)?function\s+{method}\s*\(|"
                rf"export\s+const\s+{method}\s*=",
                content,
            ))
            checks.append({
                "check": "method_exported",
                "passed": has_method,
                "message": f"Handler exports {method}" if has_method else f"{method} handler not found in route file",
            })
        else:
            # Pages router: check for method switching in default handler
            has_method_check = bool(re.search(
                rf"""req\.method\s*===?\s*['"]{method}['"]""",
                content,
            ))
            has_default = bool(re.search(r"export\s+default", content))
            checks.append({
                "check": "method_handled",
                "passed": has_method_check or has_default,
                "message": f"Handler checks for {method}" if has_method_check else "Default handler (no method check)",
            })

        # Check 3: Middleware matches?
        mw_result = self.get_middleware_chain(url)
        if mw_result.get("status") == "ok" and mw_result.get("middleware"):
            mw = mw_result["middleware"]
            matches = mw.get("matches_route", True)
            checks.append({
                "check": "middleware_matches",
                "passed": True,
                "matches_route": matches,
                "logic": mw.get("logic", []),
            })
        else:
            checks.append({
                "check": "middleware_matches",
                "passed": True,
                "message": "No middleware configured",
            })

        # Check 4: Auth check present?
        has_auth = bool(re.search(
            r"getServerSession|getToken|auth\(\)|getSession|"
            r"headers\(\).*(?:authorization|bearer)|cookies\(\)|"
            r"withAuth|requireAuth|checkAuth|isAuthenticated",
            content, re.IGNORECASE,
        ))
        checks.append({
            "check": "auth_present",
            "passed": has_auth,
            "message": "Auth check found" if has_auth else "No auth check detected — may be intentional for public endpoints",
        })

        # Check 5: Input validation present?
        has_validation = bool(re.search(
            r"\.parse\(|\.safeParse\(|\.validate\(|\.validateSync\(|"
            r"body\s*=\s*\w+Schema|zod|yup|joi|superstruct",
            content, re.IGNORECASE,
        ))
        checks.append({
            "check": "input_validation",
            "passed": has_validation,
            "message": "Input validation found" if has_validation else "No input validation detected",
        })

        # Check 6: Error handling?
        has_error_handling = bool(re.search(r"\btry\s*\{", content))
        checks.append({
            "check": "error_handling",
            "passed": has_error_handling,
            "message": "try/catch error handling found" if has_error_handling else "No try/catch error handling",
        })

        overall = "pass" if all(c["passed"] for c in checks) else "warn"
        critical_fails = [c for c in checks if not c["passed"] and c["check"] in ("route_exists", "method_exported", "method_handled")]
        if critical_fails:
            overall = "fail"

        return {
            "status": "ok",
            "method": method,
            "url": url,
            "checks": checks,
            "overall": overall,
        }

    # ── 10. get_project_conventions ───────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real patterns for:
        - 'naming': component/file naming conventions
        - 'state': state management patterns
        - 'data-fetching': how data is fetched
        - 'styling': Tailwind/CSS modules/styled-components
        - 'testing': testing patterns
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        result: Dict[str, Any] = {"pattern_type": pattern_type}

        if pattern_type == "naming":
            result.update(self._analyze_naming_conventions(base, src))
        elif pattern_type == "state":
            result.update(self._analyze_state_conventions(base, src))
        elif pattern_type == "data-fetching":
            result.update(self._analyze_data_fetching_conventions(base, src))
        elif pattern_type == "styling":
            result.update(self._analyze_styling_conventions(base, src))
        elif pattern_type == "testing":
            result.update(self._analyze_testing_conventions(base, src))
        else:
            return {
                "status": "error",
                "message": f"Unknown pattern_type '{pattern_type}'. Use: naming, state, data-fetching, styling, testing",
            }

        return {"status": "ok", **result}

    def _analyze_naming_conventions(self, base: str, src: str) -> Dict[str, Any]:
        """Analyze file and component naming conventions."""
        conventions: Dict[str, Any] = {
            "file_naming": {"kebab-case": 0, "camelCase": 0, "PascalCase": 0, "snake_case": 0},
            "component_naming": {"PascalCase": 0, "other": 0},
            "examples": [],
            "directory_structure": [],
        }

        component_dirs = self._get_component_dirs(base)
        for cdir in component_dirs:
            rel_dir = os.path.relpath(cdir, base)
            dir_files = []
            for fpath in self._walk_files(cdir):
                fname = os.path.basename(fpath)
                name_no_ext = re.sub(r"\.(tsx?|jsx?)$", "", fname)

                # Classify file naming
                if "-" in name_no_ext:
                    conventions["file_naming"]["kebab-case"] += 1
                elif "_" in name_no_ext:
                    conventions["file_naming"]["snake_case"] += 1
                elif name_no_ext[0:1].isupper():
                    conventions["file_naming"]["PascalCase"] += 1
                elif name_no_ext[0:1].islower() and name_no_ext != name_no_ext.lower():
                    conventions["file_naming"]["camelCase"] += 1

                content = self._read_file(fpath)
                if content:
                    comp_name = self._extract_component_name(content, fpath)
                    if comp_name and comp_name[0].isupper():
                        conventions["component_naming"]["PascalCase"] += 1
                    elif comp_name:
                        conventions["component_naming"]["other"] += 1

                dir_files.append(fname)

            if dir_files:
                conventions["directory_structure"].append({
                    "dir": rel_dir,
                    "sample_files": dir_files[:5],
                    "total": len(dir_files),
                })

        # Determine dominant naming convention
        max_style = max(conventions["file_naming"], key=conventions["file_naming"].get)
        conventions["dominant_file_naming"] = max_style

        # Check for barrel exports (index.ts re-exports)
        barrel_count = 0
        for cdir in component_dirs:
            for fpath in self._walk_files(cdir):
                if os.path.basename(fpath).startswith("index."):
                    content = self._read_file(fpath)
                    if content and re.search(r"export\s+\{.*\}\s+from|export\s+\*\s+from", content):
                        barrel_count += 1
        conventions["uses_barrel_exports"] = barrel_count > 0
        conventions["barrel_export_count"] = barrel_count

        return conventions

    def _analyze_state_conventions(self, base: str, src: str) -> Dict[str, Any]:
        """Analyze state management conventions."""
        sm = self.get_state_management()
        if sm.get("status") != "ok":
            return {"message": "Could not analyze state management"}

        conventions: Dict[str, Any] = {
            "libraries_used": list(sm.get("stores", {}).keys()),
            "store_count": sm.get("summary", {}),
            "dominant_library": None,
        }

        # Determine dominant library
        summary = sm.get("summary", {})
        if summary:
            conventions["dominant_library"] = max(summary, key=summary.get)

        # Check package.json for state deps
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            pkg_content = self._read_file(pkg_path)
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    state_libs = {
                        "zustand": deps.get("zustand"),
                        "@reduxjs/toolkit": deps.get("@reduxjs/toolkit"),
                        "redux": deps.get("redux"),
                        "recoil": deps.get("recoil"),
                        "jotai": deps.get("jotai"),
                        "mobx": deps.get("mobx"),
                        "valtio": deps.get("valtio"),
                        "@tanstack/react-query": deps.get("@tanstack/react-query"),
                        "swr": deps.get("swr"),
                    }
                    conventions["installed_state_libs"] = {k: v for k, v in state_libs.items() if v}
                except json.JSONDecodeError:
                    pass

        return conventions

    def _analyze_data_fetching_conventions(self, base: str, src: str) -> Dict[str, Any]:
        """Analyze data fetching conventions across the project."""
        conventions: Dict[str, Any] = {
            "patterns_found": {
                "server_components_with_fetch": 0,
                "client_components_with_hooks": 0,
                "getServerSideProps": 0,
                "getStaticProps": 0,
                "server_actions": 0,
                "react_query": 0,
                "swr": 0,
                "prisma_direct": 0,
                "trpc": 0,
            },
            "examples": [],
        }

        scan_dir = os.path.join(base, src) if src else base
        files = self._walk_files(scan_dir)
        sample_count = 0

        for fpath in files:
            content = self._read_file(fpath)
            if not content:
                continue

            rel = os.path.relpath(fpath, base)
            is_client = self._is_client_component(content)

            if not is_client and re.search(r"(?:await\s+)?fetch\s*\(", content):
                conventions["patterns_found"]["server_components_with_fetch"] += 1
            if is_client and re.search(r"useQuery|useSWR|useMutation", content):
                conventions["patterns_found"]["client_components_with_hooks"] += 1
            if re.search(r"getServerSideProps", content):
                conventions["patterns_found"]["getServerSideProps"] += 1
            if re.search(r"getStaticProps", content):
                conventions["patterns_found"]["getStaticProps"] += 1
            if re.search(r"""['"]use server['"]""", content):
                conventions["patterns_found"]["server_actions"] += 1
            if re.search(r"useQuery|useSuspenseQuery|useInfiniteQuery|useMutation", content):
                conventions["patterns_found"]["react_query"] += 1
            if re.search(r"useSWR|useSWRInfinite", content):
                conventions["patterns_found"]["swr"] += 1
            if re.search(r"prisma\.\w+\.\w+", content):
                conventions["patterns_found"]["prisma_direct"] += 1
            if re.search(r"trpc|createTRPCRouter|t\.router|api\.\w+\.\w+\.useQuery", content):
                conventions["patterns_found"]["trpc"] += 1

            # Collect some examples
            if sample_count < 5 and re.search(r"fetch\(|useQuery|useSWR|prisma\.", content):
                conventions["examples"].append(rel)
                sample_count += 1

        # Determine dominant pattern
        dominant = max(conventions["patterns_found"], key=conventions["patterns_found"].get)
        conventions["dominant_pattern"] = dominant if conventions["patterns_found"][dominant] > 0 else "none"

        return conventions

    def _analyze_styling_conventions(self, base: str, src: str) -> Dict[str, Any]:
        """Analyze styling conventions."""
        conventions: Dict[str, Any] = {
            "methods_detected": [],
            "file_counts": {},
        }

        scan_dir = os.path.join(base, src) if src else base

        # Check for Tailwind
        tailwind_config = None
        for tc in ["tailwind.config.js", "tailwind.config.ts", "tailwind.config.mjs", "tailwind.config.cjs"]:
            if os.path.isfile(os.path.join(base, tc)):
                tailwind_config = tc
                break

        if tailwind_config:
            conventions["methods_detected"].append("tailwind")
            conventions["tailwind_config"] = tailwind_config

        # Check postcss.config
        for pc in ["postcss.config.js", "postcss.config.mjs", "postcss.config.cjs"]:
            if os.path.isfile(os.path.join(base, pc)):
                conventions["postcss_config"] = pc
                break

        # Count CSS file types
        css_module_count = 0
        css_count = 0
        scss_count = 0
        styled_count = 0

        for root, _, files in os.walk(scan_dir):
            for fname in files:
                if fname.endswith(".module.css") or fname.endswith(".module.scss"):
                    css_module_count += 1
                elif fname.endswith(".css"):
                    css_count += 1
                elif fname.endswith(".scss") or fname.endswith(".sass"):
                    scss_count += 1

        conventions["file_counts"] = {
            "css_modules": css_module_count,
            "css": css_count,
            "scss": scss_count,
        }

        if css_module_count > 0:
            conventions["methods_detected"].append("css-modules")
        if scss_count > 0:
            conventions["methods_detected"].append("scss")

        # Scan a sample of component files for styling patterns
        sample_files = self._walk_files(scan_dir, (".tsx", ".jsx"))[:50]
        tailwind_usage = 0
        styled_components = 0
        emotion_usage = 0
        inline_styles = 0

        for fpath in sample_files:
            content = self._read_file(fpath)
            if not content:
                continue

            if re.search(r'className\s*=\s*[{"\'].*(?:flex|grid|p-|m-|text-|bg-|w-|h-)', content):
                tailwind_usage += 1
            if re.search(r"styled\.\w+|styled\(", content):
                styled_components += 1
            if re.search(r"css`|@emotion|css\(\{", content):
                emotion_usage += 1
            if re.search(r"style\s*=\s*\{\{", content):
                inline_styles += 1

        if styled_components > 0:
            conventions["methods_detected"].append("styled-components")
        if emotion_usage > 0:
            conventions["methods_detected"].append("emotion")
        if inline_styles > 2:
            conventions["methods_detected"].append("inline-styles")

        conventions["usage_counts"] = {
            "tailwind_class_files": tailwind_usage,
            "styled_components_files": styled_components,
            "emotion_files": emotion_usage,
            "inline_style_files": inline_styles,
        }

        # Check for UI library
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            pkg_content = self._read_file(pkg_path)
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    ui_libs = {}
                    for lib in [
                        "@radix-ui/react-*", "shadcn", "@headlessui/react",
                        "@mui/material", "@chakra-ui/react", "antd",
                        "@mantine/core", "flowbite-react", "daisyui",
                    ]:
                        if lib.endswith("*"):
                            prefix = lib.rstrip("*")
                            matches = [k for k in deps if k.startswith(prefix)]
                            if matches:
                                ui_libs[prefix.rstrip("/")] = f"{len(matches)} packages"
                        elif lib in deps:
                            ui_libs[lib] = deps[lib]
                    conventions["ui_libraries"] = ui_libs
                except json.JSONDecodeError:
                    pass

        return conventions

    def _analyze_testing_conventions(self, base: str, src: str) -> Dict[str, Any]:
        """Analyze testing conventions."""
        conventions: Dict[str, Any] = {
            "framework": None,
            "test_file_count": 0,
            "patterns": {},
            "test_directories": [],
        }

        # Check for test config files
        test_configs = {
            "jest": ["jest.config.js", "jest.config.ts", "jest.config.mjs"],
            "vitest": ["vitest.config.ts", "vitest.config.js", "vitest.config.mts"],
            "playwright": ["playwright.config.ts", "playwright.config.js"],
            "cypress": ["cypress.config.ts", "cypress.config.js", "cypress.json"],
        }

        for framework, configs in test_configs.items():
            for cfg in configs:
                if os.path.isfile(os.path.join(base, cfg)):
                    conventions["framework"] = framework
                    conventions["config_file"] = cfg
                    break
            if conventions["framework"]:
                break

        # Find test files
        test_patterns = {
            "*.test.ts": 0, "*.test.tsx": 0, "*.test.js": 0, "*.test.jsx": 0,
            "*.spec.ts": 0, "*.spec.tsx": 0, "*.spec.js": 0, "*.spec.jsx": 0,
        }
        test_dirs: Set[str] = set()

        for root, dirs, files in os.walk(base):
            # Skip node_modules
            if "node_modules" in root or ".next" in root:
                continue
            for fname in files:
                for pattern_key in test_patterns:
                    suffix = pattern_key.lstrip("*")
                    if fname.endswith(suffix):
                        test_patterns[pattern_key] += 1
                        conventions["test_file_count"] += 1
                        test_dirs.add(os.path.relpath(root, base))
                        break

        conventions["file_patterns"] = {k: v for k, v in test_patterns.items() if v > 0}
        conventions["test_directories"] = sorted(test_dirs)[:10]

        # Check for testing-library
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            pkg_content = self._read_file(pkg_path)
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    testing_libs = {}
                    for lib in [
                        "@testing-library/react", "@testing-library/jest-dom",
                        "@testing-library/user-event", "msw", "@playwright/test",
                        "cypress", "jest", "vitest", "happy-dom", "jsdom",
                    ]:
                        if lib in deps:
                            testing_libs[lib] = deps[lib]
                    conventions["testing_libraries"] = testing_libs

                    # Check for test scripts
                    scripts = pkg.get("scripts", {})
                    test_scripts = {k: v for k, v in scripts.items() if "test" in k.lower()}
                    conventions["test_scripts"] = test_scripts
                except json.JSONDecodeError:
                    pass

        # Determine naming convention (.test vs .spec)
        test_total = sum(v for k, v in test_patterns.items() if ".test." in k)
        spec_total = sum(v for k, v in test_patterns.items() if ".spec." in k)
        if test_total > spec_total:
            conventions["naming_convention"] = ".test"
        elif spec_total > test_total:
            conventions["naming_convention"] = ".spec"
        elif test_total > 0:
            conventions["naming_convention"] = "mixed"
        else:
            conventions["naming_convention"] = "none"

        return conventions

    # ── 11. get_similar_patterns ──────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.
        Matches against a registry of common UI/logic patterns.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        description_lower = description.lower()

        # Find which registered patterns match the description
        matched_patterns: List[str] = []
        for pattern_name, pattern_def in self.PATTERN_REGISTRY.items():
            for kw in pattern_def["keywords"]:
                if kw in description_lower:
                    matched_patterns.append(pattern_name)
                    break

        # If no registered pattern matched, search generically
        if not matched_patterns:
            matched_patterns = ["_generic"]

        results: Dict[str, Any] = {
            "description": description,
            "matched_patterns": [],
        }

        scan_dir = os.path.join(base, src) if src else base
        all_files = self._walk_files(scan_dir, (".tsx", ".jsx", ".ts", ".js"))

        for pattern_name in matched_patterns:
            if pattern_name == "_generic":
                # Generic search: look for the description words in file names and content
                words = [w for w in re.findall(r"\w+", description_lower) if len(w) > 2]
                matching_files = []

                for fpath in all_files:
                    fname_lower = os.path.basename(fpath).lower()
                    rel = os.path.relpath(fpath, base)

                    # Check filename
                    name_match = any(w in fname_lower for w in words)
                    if name_match:
                        content = self._read_file(fpath)
                        hooks = self._extract_hooks(content) if content else []
                        matching_files.append({
                            "file": rel,
                            "match_type": "filename",
                            "hooks": hooks[:5],
                        })
                        continue

                    # Check content (limited to avoid perf issues)
                    if len(matching_files) < 20:
                        content = self._read_file(fpath)
                        if content:
                            content_lower = content.lower()
                            if sum(1 for w in words if w in content_lower) >= len(words) * 0.5:
                                matching_files.append({
                                    "file": rel,
                                    "match_type": "content",
                                    "hooks": self._extract_hooks(content)[:5],
                                })

                if matching_files:
                    results["matched_patterns"].append({
                        "pattern": "generic-search",
                        "description": description,
                        "matching_files": matching_files[:15],
                    })
            else:
                pattern_def = self.PATTERN_REGISTRY[pattern_name]
                matching_files = []

                for fpath in all_files:
                    fname_lower = os.path.basename(fpath).lower()
                    rel = os.path.relpath(fpath, base)

                    # Check filename patterns
                    file_match = any(
                        re.search(fp, fname_lower, re.IGNORECASE)
                        for fp in pattern_def["file_patterns"]
                    )

                    content = None
                    code_matches = []

                    if file_match or len(matching_files) < 30:
                        content = self._read_file(fpath)
                        if content:
                            for cp in pattern_def["code_patterns"]:
                                if re.search(cp, content):
                                    code_matches.append(cp)

                    if file_match or code_matches:
                        hooks = self._extract_hooks(content) if content else []
                        props = self._extract_props_interface(content) if content else []
                        entry: Dict[str, Any] = {
                            "file": rel,
                            "match_type": "filename" if file_match else "code",
                            "code_patterns_matched": len(code_matches),
                            "hooks": hooks[:5],
                        }
                        if props:
                            entry["props"] = [p["name"] for p in props[:10]]
                        matching_files.append(entry)

                if matching_files:
                    # Sort by relevance (more code pattern matches = more relevant)
                    matching_files.sort(key=lambda x: x.get("code_patterns_matched", 0), reverse=True)
                    results["matched_patterns"].append({
                        "pattern": pattern_name,
                        "description": f"Existing {pattern_name} implementations",
                        "matching_files": matching_files[:15],
                    })

        results["total_matches"] = sum(
            len(p["matching_files"]) for p in results["matched_patterns"]
        )

        return {"status": "ok", **results}

    # ── 12. get_project_snapshot ──────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Compact overview: component count by dir, API routes, Prisma models,
        pages, middleware, state stores, package.json scripts/deps.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        snapshot: Dict[str, Any] = {}

        # package.json
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            pkg_content = self._read_file(pkg_path)
            if pkg_content:
                try:
                    pkg = json.loads(pkg_content)
                    snapshot["project_name"] = pkg.get("name", "unknown")
                    snapshot["scripts"] = list(pkg.get("scripts", {}).keys())
                    deps = pkg.get("dependencies", {})
                    dev_deps = pkg.get("devDependencies", {})

                    # Key framework versions
                    key_deps = {}
                    for dep in ["next", "react", "react-dom", "typescript", "prisma", "@prisma/client",
                                "tailwindcss", "zustand", "@reduxjs/toolkit", "@tanstack/react-query",
                                "swr", "trpc", "@trpc/server", "zod", "next-auth", "@auth/core"]:
                        if dep in deps:
                            key_deps[dep] = deps[dep]
                        elif dep in dev_deps:
                            key_deps[dep] = dev_deps[dep] + " (dev)"

                    snapshot["key_dependencies"] = key_deps
                    snapshot["total_dependencies"] = len(deps)
                    snapshot["total_dev_dependencies"] = len(dev_deps)
                except json.JSONDecodeError:
                    pass

        # tsconfig.json
        tsconfig_path = os.path.join(base, "tsconfig.json")
        if os.path.isfile(tsconfig_path):
            snapshot["has_typescript"] = True
            tc_content = self._read_file(tsconfig_path)
            if tc_content:
                try:
                    # Handle JSON with comments (strip // comments)
                    cleaned = re.sub(r"//.*$", "", tc_content, flags=re.MULTILINE)
                    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
                    # Handle trailing commas
                    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
                    tc = json.loads(cleaned)
                    paths = tc.get("compilerOptions", {}).get("paths", {})
                    if paths:
                        snapshot["path_aliases"] = list(paths.keys())
                except (json.JSONDecodeError, Exception):
                    pass
        else:
            snapshot["has_typescript"] = False

        # Component count by directory
        component_counts: Dict[str, int] = {}
        component_dirs = self._get_component_dirs(base)
        for cdir in component_dirs:
            count = 0
            for fpath in self._walk_files(cdir, (".tsx", ".jsx")):
                content = self._read_file(fpath)
                if content and self._is_component_file(content):
                    count += 1
            if count > 0:
                component_counts[os.path.relpath(cdir, base)] = count
        snapshot["components"] = component_counts
        snapshot["total_components"] = sum(component_counts.values())

        # API routes
        api_result = self.get_api_routes()
        if api_result.get("status") == "ok":
            snapshot["api_routes"] = {
                "total": api_result["total"],
                "app_router": api_result["app_router_count"],
                "pages_router": api_result["pages_router_count"],
                "routes": [r["path"] for r in api_result.get("routes", [])[:20]],
            }

        # Pages (App Router)
        app_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
        pages: List[str] = []
        layouts: List[str] = []
        if os.path.isdir(app_dir):
            for fpath in self._walk_files(app_dir):
                fname = os.path.basename(fpath)
                rel = os.path.relpath(fpath, base)
                if re.match(r"page\.(tsx?|jsx?)$", fname):
                    pages.append(rel)
                elif re.match(r"layout\.(tsx?|jsx?)$", fname):
                    layouts.append(rel)

        # Pages Router pages
        pages_dir = os.path.join(base, "pages")
        if os.path.isdir(pages_dir):
            for fpath in self._walk_files(pages_dir):
                fname = os.path.basename(fpath)
                if not fname.startswith("_") and "api/" not in os.path.relpath(fpath, pages_dir):
                    pages.append(os.path.relpath(fpath, base))

        snapshot["pages"] = pages[:30]
        snapshot["layouts"] = layouts[:15]
        snapshot["total_pages"] = len(pages)

        # Prisma
        prisma_result = self.get_prisma_schema()
        if prisma_result.get("status") == "ok":
            snapshot["prisma"] = {
                "models": list(prisma_result.get("models", {}).keys()),
                "enums": list(prisma_result.get("enums", {}).keys()),
                "total_models": prisma_result["total_models"],
                "total_enums": prisma_result["total_enums"],
            }

        # Middleware
        mw_result = self.get_middleware_chain()
        if mw_result.get("status") == "ok" and mw_result.get("middleware"):
            snapshot["middleware"] = {
                "file": mw_result["middleware"].get("file"),
                "matchers": mw_result["middleware"].get("matchers", []),
            }

        # State management
        sm_result = self.get_state_management()
        if sm_result.get("status") == "ok":
            snapshot["state_management"] = sm_result.get("summary", {})

        # Environment files
        env_files = []
        for ef in [".env", ".env.local", ".env.development", ".env.production", ".env.test"]:
            if os.path.isfile(os.path.join(base, ef)):
                env_files.append(ef)
        snapshot["env_files"] = env_files

        # next.config
        for nc in ["next.config.js", "next.config.mjs", "next.config.ts"]:
            if os.path.isfile(os.path.join(base, nc)):
                snapshot["next_config"] = nc
                break

        return {"status": "ok", "snapshot": snapshot}

    # ── 13. get_validation_chain ──────────────────────────────────────

    def get_validation_chain(self, route_path: str) -> Dict[str, Any]:
        """
        Trace: Zod/Yup schema -> API route handler validation -> form component fields.
        Detect mismatches between schema fields and form inputs.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        src = self._find_src_root(base)
        result: Dict[str, Any] = {
            "route": route_path,
            "schema": None,
            "handler_validation": None,
            "form_fields": None,
            "mismatches": [],
        }

        # Step 1: Find the route handler
        app_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
        route_file = None

        if os.path.isfile(os.path.join(base, route_path)):
            route_file = os.path.join(base, route_path)
        elif os.path.isdir(app_dir):
            for fpath in self._walk_files(app_dir):
                if not re.match(r"route\.(ts|js|tsx|jsx)$", os.path.basename(fpath)):
                    continue
                rel = os.path.relpath(fpath, app_dir)
                rp = "/" + os.path.dirname(rel).replace("\\", "/")
                rp = re.sub(r"\(.*?\)/", "", rp)
                rp = re.sub(r"/+$", "", rp) or "/"
                if rp == route_path:
                    route_file = fpath
                    break

        if not route_file:
            return {"status": "error", "message": f"Route not found: {route_path}"}

        content = self._read_file(route_file)
        if not content:
            return {"status": "error", "message": "Could not read route file"}

        # Step 2: Find validation schema references
        schema_refs: List[Dict[str, Any]] = []

        # Zod: someSchema.parse(body) / someSchema.safeParse(body)
        zod_refs = re.findall(r"(\w+)\s*\.(?:parse|safeParse|parseAsync)\s*\(", content)
        for ref in zod_refs:
            schema_refs.append({"name": ref, "type": "zod", "method": "parse"})

        # Yup: someSchema.validate(body)
        yup_refs = re.findall(r"(\w+)\s*\.(?:validate|validateSync|cast)\s*\(", content)
        for ref in yup_refs:
            if ref not in [r["name"] for r in schema_refs]:
                schema_refs.append({"name": ref, "type": "yup", "method": "validate"})

        result["handler_validation"] = schema_refs

        # Step 3: Find schema definitions (in same file or imported)
        schema_fields: Dict[str, List[Dict[str, Any]]] = {}

        for ref in schema_refs:
            schema_name = ref["name"]
            # Look in current file first
            fields = self._extract_zod_fields(content, schema_name)
            if not fields:
                # Look in imported files
                imports = self._extract_imports(content)
                for imp in imports:
                    if schema_name in imp.get("named", []) or schema_name == imp.get("default"):
                        # Resolve import path
                        imp_path = self._resolve_import_path(
                            os.path.dirname(route_file), imp["source"], base
                        )
                        if imp_path:
                            imp_content = self._read_file(imp_path)
                            if imp_content:
                                fields = self._extract_zod_fields(imp_content, schema_name)
                                break

            if fields:
                schema_fields[schema_name] = fields
                result["schema"] = {
                    "name": schema_name,
                    "file": os.path.relpath(route_file, base),
                    "fields": fields,
                }

        # Step 4: Find form components that submit to this route
        form_fields: List[Dict[str, Any]] = []
        scan_dirs = self._get_component_dirs(base)
        app_pages_dir = os.path.join(base, src, "app") if src else os.path.join(base, "app")
        if os.path.isdir(app_pages_dir):
            scan_dirs.append(app_pages_dir)

        route_path_escaped = re.escape(route_path)
        for sdir in scan_dirs:
            for fpath in self._walk_files(sdir, (".tsx", ".jsx")):
                fcontent = self._read_file(fpath)
                if not fcontent:
                    continue

                # Check if form submits to our route
                if not re.search(rf"""(?:action|fetch|axios|api)\s*[=(]\s*['"`]{route_path_escaped}""", fcontent):
                    # Also check for the route in fetch calls
                    if not re.search(rf"""fetch\s*\(\s*['"`][^'"`]*{route_path_escaped}""", fcontent):
                        continue

                # Extract form input fields
                input_fields = re.findall(
                    r"""(?:name|id)\s*=\s*['"](\w+)['"]""",
                    fcontent,
                )
                # Extract register() calls (react-hook-form)
                register_fields = re.findall(
                    r"""register\s*\(\s*['"](\w+)['"]""",
                    fcontent,
                )

                all_fields = list(set(input_fields + register_fields))
                if all_fields:
                    form_fields.append({
                        "file": os.path.relpath(fpath, base),
                        "fields": all_fields,
                    })

        result["form_fields"] = form_fields

        # Step 5: Detect mismatches
        if schema_fields and form_fields:
            for schema_name, s_fields in schema_fields.items():
                schema_field_names = {f["name"] for f in s_fields}
                for form in form_fields:
                    form_field_names = set(form["fields"])

                    # Fields in form but not in schema
                    extra_in_form = form_field_names - schema_field_names
                    if extra_in_form:
                        result["mismatches"].append({
                            "type": "form_field_not_in_schema",
                            "form_file": form["file"],
                            "schema": schema_name,
                            "fields": list(extra_in_form),
                        })

                    # Required fields in schema but not in form
                    required_schema = {f["name"] for f in s_fields if not f.get("optional", False)}
                    missing_in_form = required_schema - form_field_names
                    if missing_in_form:
                        result["mismatches"].append({
                            "type": "required_schema_field_not_in_form",
                            "form_file": form["file"],
                            "schema": schema_name,
                            "fields": list(missing_in_form),
                        })

        return {"status": "ok", **result}

    def _extract_zod_fields(self, content: str, schema_name: str) -> List[Dict[str, Any]]:
        """Extract field definitions from a Zod schema."""
        fields = []

        # Find the schema definition: const schema = z.object({ ... })
        pattern = re.compile(
            rf"{re.escape(schema_name)}\s*=\s*z\.object\s*\(\s*\{{([\s\S]*?)\}}\s*\)",
        )
        m = pattern.search(content)
        if not m:
            # Try without z.object wrapper
            pattern2 = re.compile(
                rf"{re.escape(schema_name)}\s*=\s*\{{([\s\S]*?)\}}",
            )
            m = pattern2.search(content)

        if not m:
            return fields

        body = m.group(1)

        # Parse fields: fieldName: z.string().optional()
        field_pattern = re.compile(r"(\w+)\s*:\s*(z\.\w+(?:\([^)]*\))?(?:\.\w+(?:\([^)]*\))?)*)")
        for fm in field_pattern.finditer(body):
            fname = fm.group(1)
            ftype_chain = fm.group(2)

            # Determine base type
            type_match = re.search(r"z\.(\w+)", ftype_chain)
            base_type = type_match.group(1) if type_match else "unknown"

            # Check for optional
            is_optional = ".optional()" in ftype_chain or ".nullish()" in ftype_chain
            is_nullable = ".nullable()" in ftype_chain or ".nullish()" in ftype_chain

            fields.append({
                "name": fname,
                "type": base_type,
                "optional": is_optional,
                "nullable": is_nullable,
                "raw": ftype_chain.strip(),
            })

        return fields

    def _resolve_import_path(self, from_dir: str, import_source: str, base: str) -> Optional[str]:
        """Resolve a relative import to an absolute file path."""
        if not import_source.startswith("."):
            return None

        candidate = os.path.normpath(os.path.join(from_dir, import_source))

        # Try with extensions
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            if os.path.isfile(candidate + ext):
                return candidate + ext

        # Try as directory with index
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            index_path = os.path.join(candidate, f"index{ext}")
            if os.path.isfile(index_path):
                return index_path

        # Already has extension
        if os.path.isfile(candidate):
            return candidate

        return None

    # ── 14. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit impact analysis:
        - component -> check consumers/props
        - API route -> check callers
        - hook -> check users
        - store -> check subscribers
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": "Could not read file"}

        src = self._find_src_root(base)
        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name,
            "file_type": None,
            "impact": [],
            "warnings": [],
            "suggestions": [],
        }

        # Determine file type
        fname = os.path.basename(full_path)
        if re.match(r"route\.(ts|js|tsx|jsx)$", fname):
            result["file_type"] = "api-route"
        elif re.match(r"page\.(ts|js|tsx|jsx)$", fname):
            result["file_type"] = "page"
        elif re.match(r"layout\.(ts|js|tsx|jsx)$", fname):
            result["file_type"] = "layout"
        elif re.match(r"middleware\.(ts|js)$", fname):
            result["file_type"] = "middleware"
        elif symbol_name.startswith("use") and symbol_name[3:4].isupper():
            result["file_type"] = "hook"
        elif re.search(r"create\(|createSlice\(|createContext\(", content):
            result["file_type"] = "store"
        elif self._is_component_file(content):
            result["file_type"] = "component"
        else:
            result["file_type"] = "utility"

        # Find all files that reference the symbol
        scan_dir = os.path.join(base, src) if src else base
        all_files = self._walk_files(scan_dir)
        referencing_files: List[Dict[str, Any]] = []

        for fpath in all_files:
            if fpath == full_path:
                continue

            fcontent = self._read_file(fpath)
            if not fcontent or symbol_name not in fcontent:
                continue

            rel = os.path.relpath(fpath, base)

            # Classify usage
            usages = []
            lines = fcontent.split("\n")
            for i, line in enumerate(lines, 1):
                if symbol_name not in line:
                    continue

                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue

                usage_type = "reference"
                if re.search(rf"import\s+.*\b{re.escape(symbol_name)}\b.*from", stripped):
                    usage_type = "import"
                elif re.search(rf"<{re.escape(symbol_name)}[\s/>]", stripped):
                    usage_type = "jsx-usage"
                elif re.search(rf"{re.escape(symbol_name)}\s*\(", stripped):
                    usage_type = "call"
                elif re.search(rf":\s*{re.escape(symbol_name)}\b", stripped):
                    usage_type = "type-annotation"
                elif re.search(rf"extends\s+{re.escape(symbol_name)}\b", stripped):
                    usage_type = "extends"

                usages.append({"line": i, "type": usage_type, "text": stripped[:100]})

            if usages:
                referencing_files.append({
                    "file": rel,
                    "usages": usages[:5],
                    "count": len(usages),
                })

        result["referencing_files"] = referencing_files[:25]
        result["total_references"] = sum(r["count"] for r in referencing_files)

        # Type-specific checks
        if result["file_type"] == "component":
            # Check if symbol is a prop
            props = self._extract_props_interface(content, symbol_name)
            prop_names = [p["name"] for p in props]
            if symbol_name in prop_names:
                result["impact"].append({
                    "type": "prop-change",
                    "message": f"'{symbol_name}' is a component prop — all parent components passing this prop will be affected",
                    "affected_files": [r["file"] for r in referencing_files if any(u["type"] == "jsx-usage" for u in r["usages"])],
                })

            # Check if it's the component itself being renamed
            comp_name = self._extract_component_name(content, full_path)
            if comp_name == symbol_name:
                result["impact"].append({
                    "type": "component-rename",
                    "message": f"Renaming component '{symbol_name}' — update all imports and JSX references",
                    "affected_count": len(referencing_files),
                })

        elif result["file_type"] == "api-route":
            # Find fetch calls to this route
            rel_route = os.path.relpath(os.path.dirname(full_path), os.path.join(base, src, "app") if src else os.path.join(base, "app"))
            route_url = "/" + rel_route.replace("\\", "/")
            route_url = re.sub(r"\(.*?\)/", "", route_url)

            result["impact"].append({
                "type": "api-change",
                "message": f"Changes to API route handler '{symbol_name}' at {route_url}",
                "route_url": route_url,
            })

            # Check if the symbol is an HTTP method handler
            if symbol_name.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                result["warnings"].append(
                    f"Modifying {symbol_name} handler — check all client-side fetch() calls to {route_url}"
                )

        elif result["file_type"] == "hook":
            hook_users = [r for r in referencing_files if any(u["type"] == "call" for u in r["usages"])]
            result["impact"].append({
                "type": "hook-change",
                "message": f"Custom hook '{symbol_name}' is used in {len(hook_users)} files",
                "users": [u["file"] for u in hook_users],
            })

            # Check return type changes
            if re.search(rf"function\s+{re.escape(symbol_name)}\s*\([^)]*\)\s*:\s*(\w+)", content):
                result["warnings"].append(
                    "Hook has explicit return type — changing return shape will cause type errors in consumers"
                )

        elif result["file_type"] == "store":
            subscribers = [r for r in referencing_files if any(u["type"] in ("call", "import") for u in r["usages"])]
            result["impact"].append({
                "type": "store-change",
                "message": f"Store '{symbol_name}' has {len(subscribers)} subscribers/consumers",
                "subscribers": [s["file"] for s in subscribers],
            })

        elif result["file_type"] == "middleware":
            result["warnings"].append(
                "Middleware changes affect ALL matched routes — test thoroughly"
            )
            mw_result = self.get_middleware_chain()
            if mw_result.get("status") == "ok" and mw_result.get("middleware"):
                matchers = mw_result["middleware"].get("matchers", [])
                result["impact"].append({
                    "type": "middleware-change",
                    "message": f"Middleware matches {len(matchers)} route pattern(s)",
                    "matchers": matchers,
                })

        elif result["file_type"] == "layout":
            result["warnings"].append(
                "Layout changes affect ALL child pages and nested layouts"
            )

        # Suggestions
        if result["total_references"] > 10:
            result["suggestions"].append(
                f"High impact: {result['total_references']} references found. Consider making changes incrementally."
            )

        if result["total_references"] == 0:
            result["suggestions"].append(
                "No external references found — safe to modify without breaking other files."
            )

        return {"status": "ok", **result}
