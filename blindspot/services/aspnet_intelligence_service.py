"""
ASP.NET Core Intelligence Service - C#/.NET-specific code analysis.

Provides DbContext relationship mapping, controller/Minimal API endpoint parsing,
EF Core migration schema extraction, Razor view dependency analysis,
middleware pipeline resolution, DI container graphing, and validation chain tracing.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class AspNetIntelligenceService(BaseService):
    """ASP.NET Core-specific code intelligence for C#/.NET projects."""

    SCAN_DIRS = {
        "controllers": "Controllers",
        "models": "Models",
        "services": "Services",
        "views": "Views",
        "middleware": "Middleware",
        "data": "Data",
        "migrations": "Migrations",
        "tests": "Tests",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"Console\.Write(Line)?\s*\(",
            "severity": "warning",
            "message": "Console.WriteLine in production code -- use ILogger<T> instead",
            "file_types": ["cs"],
        },
        {
            "pattern": r"\.Result\b",
            "severity": "error",
            "message": "Task.Result blocks the thread -- use await instead",
            "file_types": ["cs"],
        },
        {
            "pattern": r'"\s*\+\s*\w+.*(?:SELECT|INSERT|UPDATE|DELETE)',
            "severity": "error",
            "message": "String concatenation in SQL -- use parameterised queries to prevent SQL injection",
            "file_types": ["cs"],
        },
        {
            "pattern": r"\[Authorize\].*missing",
            "severity": "warning",
            "message": "[Authorize] missing on controller -- verify access control",
            "file_types": ["cs"],
        },
    ]

    PATTERN_REGISTRY = {
        "middleware": {
            "patterns": [
                r"app\.Use\w*\s*\(",
                r"class\s+\w+\s*:\s*IMiddleware",
                r"public\s+async\s+Task\s+InvokeAsync\s*\(",
            ],
            "description": "ASP.NET Core middleware pipeline patterns",
        },
        "filter": {
            "patterns": [
                r"class\s+\w+\s*:\s*(?:IActionFilter|IAsyncActionFilter|IResultFilter|IExceptionFilter)",
                r"\[ServiceFilter\s*\(",
                r"\[TypeFilter\s*\(",
            ],
            "description": "ASP.NET Core action/result/exception filter patterns",
        },
        "health-check": {
            "patterns": [
                r"class\s+\w+\s*:\s*IHealthCheck",
                r"services\.AddHealthChecks\s*\(",
                r"app\.MapHealthChecks\s*\(",
            ],
            "description": "Health check endpoint patterns",
        },
        "background-service": {
            "patterns": [
                r"class\s+\w+\s*:\s*(?:BackgroundService|IHostedService)",
                r"protected\s+override\s+async\s+Task\s+ExecuteAsync\s*\(",
                r"services\.AddHostedService\s*<",
            ],
            "description": "Hosted/background service patterns",
        },
        "signalr-hub": {
            "patterns": [
                r"class\s+\w+\s*:\s*Hub\b",
                r"app\.MapHub\s*<",
                r"Clients\.All\.SendAsync\s*\(",
            ],
            "description": "SignalR hub and real-time communication patterns",
        },
        "grpc-service": {
            "patterns": [
                r"class\s+\w+\s*:\s*\w+\.?\w+Base\b",
                r"services\.AddGrpc\s*\(",
                r"app\.MapGrpcService\s*<",
            ],
            "description": "gRPC service patterns",
        },
        "rate-limiting": {
            "patterns": [
                r"services\.AddRateLimiter\s*\(",
                r"\.AddFixedWindowLimiter\s*\(",
                r"\.AddSlidingWindowLimiter\s*\(",
                r"\[EnableRateLimiting\s*\(",
            ],
            "description": "Rate limiting middleware patterns",
        },
    }

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
        return None

    def _scan_files(self, base: str, directory: str,
                    extensions: tuple = (".cs",)) -> List[dict]:
        """Scan directory recursively for C# files.

        Returns list of dicts with 'path', 'rel_path', and 'content'.
        """
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in ("bin", "obj", ".git", "node_modules", ".vs")]
            for fname in files:
                if not fname.endswith(extensions):
                    continue
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, base)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    results.append({"path": fpath, "rel_path": rel_path, "content": content})
                except Exception:
                    continue
        return results

    def _scan_all_cs_files(self, base: str) -> List[dict]:
        """Scan all C# files in the project, excluding bin/obj."""
        all_files: List[dict] = []
        seen: Set[str] = set()

        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        extra_dirs = [".", "src", "Api", "Application", "Domain", "Infrastructure",
                      "Persistence", "Web", "Hubs", "BackgroundJobs"]
        for d in extra_dirs:
            for f in self._scan_files(base, d):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    def _scan_razor_files(self, base: str) -> List[dict]:
        """Scan for Razor view files (.cshtml, .razor)."""
        results: List[dict] = []
        seen: Set[str] = set()
        for d in ["Views", "Pages", "Components", "Shared", "."]:
            for f in self._scan_files(base, d, extensions=(".cshtml", ".razor")):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    results.append(f)
        return results

    def _extract_brace_block(self, content: str, start: int) -> str:
        """Extract content inside matching braces starting at position 'start'."""
        if start >= len(content) or content[start] != "{":
            return ""
        depth = 0
        pos = start
        while pos < len(content):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
                if depth == 0:
                    return content[start + 1:pos]
            pos += 1
        return content[start + 1:]

    def _find_startup_files(self, base: str) -> List[dict]:
        """Find Program.cs and Startup.cs files."""
        results = []
        for fname in ["Program.cs", "Startup.cs"]:
            fpath = os.path.join(base, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    results.append({
                        "path": fpath,
                        "rel_path": fname,
                        "content": content,
                    })
                except Exception:
                    continue
        # Also search in src/ or Api/ subdirectories
        for subdir in ["src", "Api", "Web"]:
            for fname in ["Program.cs", "Startup.cs"]:
                fpath = os.path.join(base, subdir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        results.append({
                            "path": fpath,
                            "rel_path": os.path.relpath(fpath, base),
                            "content": content,
                        })
                    except Exception:
                        continue
        return results

    # ── 1. get_entity_relationships ────────────────────────────────────

    def get_entity_relationships(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse DbContext DbSet<T>, entity configuration (HasOne/HasMany/WithOne/WithMany),
        [Key]/[ForeignKey]/[Required] attributes, and Fluent API in OnModelCreating.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        entities: Dict[str, Any] = {}
        dbcontext_info: Dict[str, Any] = {}

        # Pattern: DbSet<EntityName> in DbContext
        dbset_pattern = re.compile(
            r"public\s+(?:virtual\s+)?DbSet\s*<\s*(\w+)\s*>\s*(\w+)\s*\{",
            re.MULTILINE,
        )

        # Pattern: class Entity with attributes
        class_pattern = re.compile(
            r"(?:\[([\w\s,\(\)=\"\']+)\]\s*)*public\s+class\s+(\w+)\s*(?::\s*([\w\s,<>]+))?\s*\{",
            re.MULTILINE,
        )

        # Attribute patterns on properties
        key_attr = re.compile(r"\[Key\]")
        fk_attr = re.compile(r'\[ForeignKey\s*\(\s*"?(\w+)"?\s*\)\]')
        required_attr = re.compile(r"\[Required\]")
        maxlen_attr = re.compile(r"\[MaxLength\s*\(\s*(\d+)\s*\)\]")
        strlen_attr = re.compile(r"\[StringLength\s*\(\s*(\d+)")
        column_attr = re.compile(r'\[Column\s*\(\s*"?(\w+)"?')
        table_attr = re.compile(r'\[Table\s*\(\s*"(\w+)"')

        # Property pattern
        prop_pattern = re.compile(
            r"((?:\[[\w\s,\(\)=\"\'\.]+\]\s*)*)public\s+(?:virtual\s+)?"
            r"([\w<>\?]+)\s+(\w+)\s*\{\s*get\s*;",
            re.MULTILINE,
        )

        # Fluent API patterns in OnModelCreating
        has_one_pattern = re.compile(
            r"entity\.HasOne\s*(?:<\s*(\w+)\s*>)?\s*\(\s*(?:e\s*=>\s*e\.)?(\w+)\s*\)",
        )
        has_many_pattern = re.compile(
            r"entity\.HasMany\s*(?:<\s*(\w+)\s*>)?\s*\(\s*(?:e\s*=>\s*e\.)?(\w+)\s*\)",
        )
        with_one_pattern = re.compile(
            r"\.WithOne\s*\(\s*(?:e\s*=>\s*e\.)?(\w+)?\s*\)",
        )
        with_many_pattern = re.compile(
            r"\.WithMany\s*\(\s*(?:e\s*=>\s*e\.)?(\w+)?\s*\)",
        )
        has_fk_pattern = re.compile(
            r"\.HasForeignKey\s*(?:<\s*\w+\s*>)?\s*\(\s*(?:e\s*=>\s*e\.)?(\w+)\s*\)",
        )

        # Pass 1: Find DbContext classes and their DbSets
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Check if this is a DbContext
            ctx_match = re.search(
                r"public\s+class\s+(\w+)\s*:\s*(?:\w+\.)*DbContext",
                content,
            )
            if ctx_match:
                ctx_name = ctx_match.group(1)
                dbsets = []
                for m in dbset_pattern.finditer(content):
                    dbsets.append({
                        "entity": m.group(1),
                        "property_name": m.group(2),
                    })
                dbcontext_info[ctx_name] = {
                    "file": rel_path,
                    "dbsets": dbsets,
                }

                # Parse OnModelCreating Fluent API
                on_model_match = re.search(
                    r"protected\s+override\s+void\s+OnModelCreating\s*\([^)]*\)\s*\{",
                    content,
                )
                if on_model_match:
                    brace_start = content.find("{", on_model_match.start())
                    model_block = self._extract_brace_block(content, brace_start)

                    # Parse entity builder blocks
                    builder_pattern = re.compile(
                        r"modelBuilder\.Entity\s*<\s*(\w+)\s*>\s*\(\s*(?:entity|e|b)\s*=>\s*\{",
                    )
                    for bm in builder_pattern.finditer(model_block):
                        target_entity = bm.group(1)
                        builder_start = model_block.find("{", bm.start())
                        builder_block = self._extract_brace_block(model_block, builder_start)

                        fluent_rels = []
                        # HasOne/HasMany with WithOne/WithMany
                        for ho in has_one_pattern.finditer(builder_block):
                            related = ho.group(1) or ho.group(2)
                            nav_prop = ho.group(2)
                            rel_info = {
                                "type": "has_one",
                                "related_entity": related,
                                "navigation_property": nav_prop,
                            }
                            # Check for WithMany/WithOne chained
                            rest = builder_block[ho.end():]
                            wm = with_many_pattern.search(rest[:200])
                            wo = with_one_pattern.search(rest[:200])
                            if wm:
                                rel_info["inverse_navigation"] = wm.group(1)
                            if wo:
                                rel_info["inverse_navigation"] = wo.group(1)
                            fk = has_fk_pattern.search(rest[:300])
                            if fk:
                                rel_info["foreign_key"] = fk.group(1)
                            fluent_rels.append(rel_info)

                        for hm in has_many_pattern.finditer(builder_block):
                            related = hm.group(1) or hm.group(2)
                            nav_prop = hm.group(2)
                            rel_info = {
                                "type": "has_many",
                                "related_entity": related,
                                "navigation_property": nav_prop,
                            }
                            rest = builder_block[hm.end():]
                            wo = with_one_pattern.search(rest[:200])
                            if wo:
                                rel_info["inverse_navigation"] = wo.group(1)
                            fk = has_fk_pattern.search(rest[:300])
                            if fk:
                                rel_info["foreign_key"] = fk.group(1)
                            fluent_rels.append(rel_info)

                        if target_entity not in entities:
                            entities[target_entity] = {
                                "file": "fluent-api",
                                "properties": [],
                                "relationships": [],
                                "attributes": [],
                            }
                        entities[target_entity]["fluent_relationships"] = fluent_rels

        # Pass 2: Parse entity classes
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            for cm in class_pattern.finditer(content):
                class_attrs_raw = cm.group(1) or ""
                class_name = cm.group(2)
                base_classes = cm.group(3) or ""

                if entity_name and class_name.lower() != entity_name.lower():
                    continue

                # Check if this is likely an entity (has DbSet reference or relevant base/attrs)
                is_entity = False
                for ctx_data in dbcontext_info.values():
                    for ds in ctx_data["dbsets"]:
                        if ds["entity"] == class_name:
                            is_entity = True
                            break

                # Also check if class has [Table] or inherits from entity base
                if table_attr.search(class_attrs_raw):
                    is_entity = True
                if any(b.strip() in ("BaseEntity", "AuditableEntity", "Entity")
                       for b in base_classes.split(",")):
                    is_entity = True

                if not is_entity and not entity_name:
                    # Check if it has typical entity properties
                    brace_start = content.find("{", cm.start())
                    block = self._extract_brace_block(content, brace_start)
                    if key_attr.search(block) or re.search(r"public\s+int\s+Id\s*\{", block):
                        is_entity = True

                if not is_entity and not entity_name:
                    continue

                brace_start = content.find("{", cm.start())
                block = self._extract_brace_block(content, brace_start)

                properties = []
                relationships = []
                entity_attrs = []

                # Parse table attribute
                table_m = table_attr.search(class_attrs_raw)
                if table_m:
                    entity_attrs.append({"type": "Table", "name": table_m.group(1)})

                # Parse properties
                for pm in prop_pattern.finditer(block):
                    attrs_raw = pm.group(1)
                    prop_type = pm.group(2)
                    prop_name = pm.group(3)

                    prop_info: Dict[str, Any] = {
                        "name": prop_name,
                        "type": prop_type,
                    }

                    # Parse attributes on property
                    if key_attr.search(attrs_raw):
                        prop_info["is_key"] = True
                    if required_attr.search(attrs_raw):
                        prop_info["required"] = True
                    fk_m = fk_attr.search(attrs_raw)
                    if fk_m:
                        prop_info["foreign_key_for"] = fk_m.group(1)
                    ml_m = maxlen_attr.search(attrs_raw)
                    if ml_m:
                        prop_info["max_length"] = int(ml_m.group(1))
                    sl_m = strlen_attr.search(attrs_raw)
                    if sl_m:
                        prop_info["string_length"] = int(sl_m.group(1))
                    col_m = column_attr.search(attrs_raw)
                    if col_m:
                        prop_info["column_name"] = col_m.group(1)

                    # Detect navigation properties (relationships)
                    clean_type = prop_type.rstrip("?")
                    is_collection = clean_type.startswith("ICollection<") or \
                                    clean_type.startswith("IList<") or \
                                    clean_type.startswith("List<") or \
                                    clean_type.startswith("IEnumerable<")
                    if is_collection:
                        inner_match = re.search(r"<\s*(\w+)\s*>", clean_type)
                        if inner_match:
                            relationships.append({
                                "property": prop_name,
                                "type": "has_many",
                                "related_entity": inner_match.group(1),
                                "collection_type": clean_type.split("<")[0],
                            })
                    elif clean_type[0:1].isupper() and clean_type not in (
                        "String", "DateTime", "DateTimeOffset", "TimeSpan",
                        "Guid", "Decimal", "Boolean", "Int32", "Int64",
                        "Double", "Single", "Byte",
                    ) and not clean_type.startswith("Nullable"):
                        # Check if there is a corresponding FK property
                        fk_name = f"{prop_name}Id"
                        has_fk = bool(re.search(
                            rf"public\s+\w+\??\s+{re.escape(fk_name)}\s*\{{",
                            block,
                        ))
                        if has_fk or fk_m:
                            relationships.append({
                                "property": prop_name,
                                "type": "belongs_to",
                                "related_entity": clean_type,
                                "foreign_key": fk_name if has_fk else (fk_m.group(1) if fk_m else None),
                            })

                    properties.append(prop_info)

                if class_name in entities:
                    entities[class_name]["file"] = rel_path
                    entities[class_name]["properties"] = properties
                    entities[class_name]["relationships"] += relationships
                    entities[class_name]["attributes"] = entity_attrs
                    entities[class_name]["base_classes"] = [
                        b.strip() for b in base_classes.split(",") if b.strip()
                    ]
                else:
                    entities[class_name] = {
                        "file": rel_path,
                        "properties": properties,
                        "relationships": relationships,
                        "attributes": entity_attrs,
                        "base_classes": [
                            b.strip() for b in base_classes.split(",") if b.strip()
                        ],
                    }

        return {
            "status": "ok",
            "entities": entities,
            "dbcontexts": dbcontext_info,
            "total_entities": len(entities),
        }

    # ── 2. get_endpoint_map ────────────────────────────────────────────

    def get_endpoint_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse [HttpGet]/[HttpPost]/[Route] attributes on controllers
        and Minimal API (app.MapGet/MapPost). Extract route, method, params.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        startup_files = self._find_startup_files(base)
        endpoints: List[Dict[str, Any]] = []

        # Controller attribute patterns
        controller_route = re.compile(
            r'\[Route\s*\(\s*"([^"]+)"\s*\)\]',
        )
        http_method_attrs = re.compile(
            r'\[(Http(?:Get|Post|Put|Delete|Patch|Head|Options))'
            r'(?:\s*\(\s*"?([^"\)]*)"?\s*\))?\]',
        )
        api_controller_attr = re.compile(r"\[ApiController\]")
        authorize_attr = re.compile(
            r'\[Authorize(?:\s*\(\s*(?:Roles\s*=\s*"([^"]+)"|Policy\s*=\s*"([^"]+)")?\s*\))?\]',
        )
        allow_anon_attr = re.compile(r"\[AllowAnonymous\]")

        # Method signature pattern
        method_sig_pattern = re.compile(
            r"public\s+(?:async\s+)?(?:Task\s*<\s*)?([\w<>]+)\s*>?\s+(\w+)\s*\(([^)]*)\)",
        )

        # Minimal API patterns
        minimal_api_pattern = re.compile(
            r'app\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*"([^"]+)"',
        )

        # Parse controllers
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Find controller classes
            ctrl_match = re.search(
                r"(?:\[([\w\s\(\)=\",\.]+)\]\s*)*"
                r"public\s+class\s+(\w+Controller)\s*:\s*([\w\s,<>]+)\s*\{",
                content,
                re.MULTILINE,
            )
            if not ctrl_match:
                continue

            ctrl_attrs_raw = content[:ctrl_match.start()]
            ctrl_name = ctrl_match.group(2)
            ctrl_base = ctrl_match.group(3)

            # Extract controller-level route
            ctrl_route = ""
            # Look at the 500 chars before the class definition for attributes
            pre_class = content[max(0, ctrl_match.start() - 500):ctrl_match.start()]
            route_m = controller_route.search(pre_class)
            if route_m:
                ctrl_route = route_m.group(1)

            is_api = bool(api_controller_attr.search(pre_class))

            # Controller-level auth
            ctrl_auth = authorize_attr.search(pre_class)
            ctrl_auth_info = None
            if ctrl_auth:
                ctrl_auth_info = {
                    "roles": ctrl_auth.group(1),
                    "policy": ctrl_auth.group(2),
                }

            # Parse method-level endpoints
            brace_start = content.find("{", ctrl_match.start())
            block = self._extract_brace_block(content, brace_start)

            # Split block into method chunks by looking for http attributes
            lines = block.split("\n")
            current_attrs: List[str] = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("["):
                    current_attrs.append(stripped)
                elif re.match(r"public\s+", stripped):
                    # Check if any preceding attrs are HTTP method attrs
                    http_method = None
                    method_route = ""
                    method_auth = None
                    is_anon = False

                    for attr_line in current_attrs:
                        hm = http_method_attrs.search(attr_line)
                        if hm:
                            http_method = hm.group(1).replace("Http", "").upper()
                            method_route = hm.group(2) or ""
                        rm = controller_route.search(attr_line)
                        if rm:
                            method_route = rm.group(1)
                        am = authorize_attr.search(attr_line)
                        if am:
                            method_auth = {"roles": am.group(1), "policy": am.group(2)}
                        if allow_anon_attr.search(attr_line):
                            is_anon = True

                    if http_method:
                        sig_m = method_sig_pattern.search(stripped)
                        method_name = sig_m.group(2) if sig_m else "unknown"
                        return_type = sig_m.group(1) if sig_m else "unknown"
                        params_raw = sig_m.group(3) if sig_m else ""

                        # Build full route
                        full_route = ctrl_route
                        if method_route:
                            if method_route.startswith("/"):
                                full_route = method_route
                            else:
                                full_route = f"{ctrl_route}/{method_route}" if ctrl_route else method_route

                        # Replace [controller] placeholder
                        full_route = full_route.replace(
                            "[controller]",
                            ctrl_name.replace("Controller", ""),
                        )
                        full_route = full_route.replace(
                            "[action]",
                            method_name,
                        )

                        # Parse parameters
                        params = []
                        if params_raw.strip():
                            for p in params_raw.split(","):
                                p = p.strip()
                                from_match = re.search(
                                    r"\[(FromBody|FromQuery|FromRoute|FromForm|FromHeader)\]",
                                    p,
                                )
                                binding = from_match.group(1) if from_match else None
                                p_clean = re.sub(r"\[[\w\(\)\"=\s]+\]\s*", "", p).strip()
                                parts = p_clean.split()
                                if len(parts) >= 2:
                                    params.append({
                                        "type": parts[0],
                                        "name": parts[1],
                                        "binding": binding,
                                    })

                        endpoint_info: Dict[str, Any] = {
                            "route": "/" + full_route.lstrip("/") if full_route else "/",
                            "http_method": http_method,
                            "controller": ctrl_name,
                            "action": method_name,
                            "return_type": return_type,
                            "parameters": params,
                            "file": rel_path,
                        }

                        if is_api:
                            endpoint_info["api_controller"] = True
                        if method_auth:
                            endpoint_info["authorization"] = method_auth
                        elif ctrl_auth_info and not is_anon:
                            endpoint_info["authorization"] = ctrl_auth_info
                        if is_anon:
                            endpoint_info["allow_anonymous"] = True

                        if filter_prefix and not endpoint_info["route"].startswith(filter_prefix):
                            current_attrs = []
                            continue

                        endpoints.append(endpoint_info)
                    current_attrs = []
                elif not stripped.startswith("[") and not stripped.startswith("//"):
                    current_attrs = []

        # Parse Minimal API endpoints from startup files
        for finfo in startup_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            for mm in minimal_api_pattern.finditer(content):
                method = mm.group(1).upper()
                route = mm.group(2)

                if filter_prefix and not route.startswith(filter_prefix):
                    continue

                # Try to extract handler info
                rest = content[mm.end():mm.end() + 500]
                handler_match = re.search(r",\s*(?:async\s+)?\(?([^)]*)\)?\s*=>|,\s*(\w+)\)", rest)
                handler = handler_match.group(1) or handler_match.group(2) if handler_match else "inline"

                endpoints.append({
                    "route": route,
                    "http_method": method,
                    "controller": "MinimalApi",
                    "action": handler[:80] if handler else "lambda",
                    "file": rel_path,
                    "minimal_api": True,
                })

        return {
            "status": "ok",
            "endpoints": endpoints,
            "total": len(endpoints),
        }

    # ── 3. get_schema_info ─────────────────────────────────────────────

    def get_schema_info(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse EF Core migrations and entity attributes. Return schema.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        migrations: List[Dict[str, Any]] = []
        schema: Dict[str, Any] = {}

        # Find migration files
        migration_files = self._scan_files(base, "Migrations")
        if not migration_files:
            # Try other common locations
            for d in ["Data/Migrations", "Infrastructure/Migrations", "Persistence/Migrations"]:
                migration_files = self._scan_files(base, d)
                if migration_files:
                    break

        # Patterns for EF Core migration methods
        create_table_pattern = re.compile(
            r'migrationBuilder\.CreateTable\s*\(\s*name:\s*"(\w+)"',
        )
        add_column_pattern = re.compile(
            r'migrationBuilder\.AddColumn\s*<\s*(\w+)\s*>\s*\(\s*name:\s*"(\w+)"\s*,\s*table:\s*"(\w+)"',
        )
        drop_column_pattern = re.compile(
            r'migrationBuilder\.DropColumn\s*\(\s*name:\s*"(\w+)"\s*,\s*table:\s*"(\w+)"',
        )
        create_index_pattern = re.compile(
            r'migrationBuilder\.CreateIndex\s*\([^)]*name:\s*"(\w+)"[^)]*table:\s*"(\w+)"'
            r'[^)]*column(?:s)?:\s*(?:"(\w+)"|\s*new\s*\[\]\s*\{([^}]+)\})',
            re.DOTALL,
        )
        add_fk_pattern = re.compile(
            r'migrationBuilder\.AddForeignKey\s*\([^)]*name:\s*"(\w+)"[^)]*table:\s*"(\w+)"'
            r'[^)]*column:\s*"(\w+)"[^)]*principalTable:\s*"(\w+)"',
            re.DOTALL,
        )

        # Column definition inside CreateTable
        column_def_pattern = re.compile(
            r'(\w+)\s*=\s*table\.Column\s*<\s*(\w+)\s*>\s*\(\s*'
            r'(?:type:\s*"([^"]+)")?\s*,?\s*'
            r'((?:nullable:\s*(?:true|false)\s*,?\s*)*'
            r'(?:maxLength:\s*\d+\s*,?\s*)*'
            r'(?:defaultValue:\s*[^,\)]+\s*,?\s*)*)',
        )

        for finfo in sorted(migration_files, key=lambda x: x["rel_path"]):
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Skip Designer files
            if ".Designer." in rel_path or "ModelSnapshot" in rel_path:
                continue

            migration_name = os.path.basename(rel_path).replace(".cs", "")

            # Parse CreateTable
            for ct in create_table_pattern.finditer(content):
                table_name = ct.group(1)

                if entity_name and entity_name.lower() not in table_name.lower():
                    continue

                # Find the columns block
                rest = content[ct.end():ct.end() + 5000]
                columns_match = re.search(r"columns:\s*table\s*=>\s*new\s*\{", rest)
                if columns_match:
                    cols_block = self._extract_brace_block(
                        rest, rest.find("{", columns_match.start())
                    )
                    columns = []
                    for col_m in column_def_pattern.finditer(cols_block):
                        col_info: Dict[str, Any] = {
                            "name": col_m.group(1),
                            "clr_type": col_m.group(2),
                        }
                        if col_m.group(3):
                            col_info["db_type"] = col_m.group(3)
                        extras = col_m.group(4)
                        if "nullable: true" in extras:
                            col_info["nullable"] = True
                        if "nullable: false" in extras:
                            col_info["nullable"] = False
                        max_len = re.search(r"maxLength:\s*(\d+)", extras)
                        if max_len:
                            col_info["max_length"] = int(max_len.group(1))
                        default_val = re.search(r"defaultValue:\s*([^,\)]+)", extras)
                        if default_val:
                            col_info["default"] = default_val.group(1).strip()
                        columns.append(col_info)

                    if table_name not in schema:
                        schema[table_name] = {
                            "columns": [],
                            "indexes": [],
                            "foreign_keys": [],
                            "migration_history": [],
                        }
                    schema[table_name]["columns"] = columns
                    schema[table_name]["migration_history"].append({
                        "migration": migration_name,
                        "operation": "create_table",
                    })

            # Parse AddColumn
            for ac in add_column_pattern.finditer(content):
                col_type = ac.group(1)
                col_name = ac.group(2)
                table = ac.group(3)

                if entity_name and entity_name.lower() not in table.lower():
                    continue

                if table not in schema:
                    schema[table] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "migration_history": [],
                    }
                schema[table]["columns"].append({
                    "name": col_name,
                    "clr_type": col_type,
                    "added_in": migration_name,
                })
                schema[table]["migration_history"].append({
                    "migration": migration_name,
                    "operation": "add_column",
                    "column": col_name,
                })

            # Parse CreateIndex
            for ci in create_index_pattern.finditer(content):
                idx_name = ci.group(1)
                table = ci.group(2)
                column = ci.group(3)
                columns_arr = ci.group(4)

                if entity_name and entity_name.lower() not in table.lower():
                    continue

                if table not in schema:
                    schema[table] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "migration_history": [],
                    }

                idx_columns = []
                if column:
                    idx_columns = [column]
                elif columns_arr:
                    idx_columns = [
                        c.strip().strip('"').strip("'")
                        for c in columns_arr.split(",")
                    ]
                schema[table]["indexes"].append({
                    "name": idx_name,
                    "columns": idx_columns,
                    "unique": "unique: true" in content[ci.start():ci.end() + 100],
                })

            # Parse AddForeignKey
            for fk in add_fk_pattern.finditer(content):
                fk_name = fk.group(1)
                table = fk.group(2)
                column = fk.group(3)
                principal_table = fk.group(4)

                if entity_name and entity_name.lower() not in table.lower():
                    continue

                if table not in schema:
                    schema[table] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "migration_history": [],
                    }
                schema[table]["foreign_keys"].append({
                    "name": fk_name,
                    "column": column,
                    "references_table": principal_table,
                })

        return {
            "status": "ok",
            "schema": schema,
            "total_tables": len(schema),
        }

    # ── 4. get_view_dependencies ───────────────────────────────────────

    def get_view_dependencies(self, view_path: str) -> Dict[str, Any]:
        """
        Parse Razor: @model, @section, @RenderBody(), @RenderSection(),
        partial views, tag helpers, view components.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, view_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"View file not found: {view_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read file: {e}"}

        result: Dict[str, Any] = {
            "file": view_path,
            "model": None,
            "layout": None,
            "sections": [],
            "render_body": False,
            "render_sections": [],
            "partial_views": [],
            "tag_helpers": [],
            "view_components": [],
            "inject_services": [],
            "html_helpers": [],
            "url_references": [],
            "scripts": [],
            "styles": [],
        }

        # @model directive
        model_match = re.search(r"@model\s+([\w<>.\?]+)", content)
        if model_match:
            result["model"] = model_match.group(1)

        # @{ Layout = "..."; }
        layout_match = re.search(r'Layout\s*=\s*"([^"]+)"', content)
        if layout_match:
            result["layout"] = layout_match.group(1)

        # @section SectionName { ... }
        for sm in re.finditer(r"@section\s+(\w+)\s*\{", content):
            result["sections"].append(sm.group(1))

        # @RenderBody()
        if re.search(r"@RenderBody\s*\(\s*\)", content):
            result["render_body"] = True

        # @RenderSection("name", required: false)
        for rs in re.finditer(r'@(?:await\s+)?RenderSection(?:Async)?\s*\(\s*"(\w+)"(?:\s*,\s*(?:required:\s*)?(true|false))?', content):
            result["render_sections"].append({
                "name": rs.group(1),
                "required": rs.group(2) != "false" if rs.group(2) else True,
            })

        # Partial views: <partial name="_PartialName" /> or @await Html.PartialAsync("_Partial")
        for pv in re.finditer(r'<partial\s+name="([^"]+)"', content):
            result["partial_views"].append(pv.group(1))
        for pv in re.finditer(r'Html\.(?:Partial|PartialAsync|RenderPartialAsync)\s*\(\s*"([^"]+)"', content):
            result["partial_views"].append(pv.group(1))

        # Tag helpers: <vc:component-name />, asp-* attributes
        for th in re.finditer(r"<([\w-]+)\s[^>]*asp-([\w-]+)=\"([^\"]+)\"", content):
            result["tag_helpers"].append({
                "element": th.group(1),
                "attribute": f"asp-{th.group(2)}",
                "value": th.group(3),
            })

        # View components: @await Component.InvokeAsync("ComponentName")
        for vc in re.finditer(r'Component\.InvokeAsync\s*\(\s*"(\w+)"', content):
            result["view_components"].append(vc.group(1))
        for vc in re.finditer(r"<vc:([\w-]+)", content):
            result["view_components"].append(vc.group(1))

        # @inject IServiceType ServiceName
        for inj in re.finditer(r"@inject\s+([\w<>.]+)\s+(\w+)", content):
            result["inject_services"].append({
                "type": inj.group(1),
                "name": inj.group(2),
            })

        # HTML helpers: @Html.ActionLink, @Url.Action, @Html.DisplayFor, @Html.EditorFor
        for hh in re.finditer(r"@Html\.(\w+)\s*\(", content):
            result["html_helpers"].append(hh.group(1))
        for ur in re.finditer(r'@Url\.Action\s*\(\s*"(\w+)"\s*(?:,\s*"(\w+)")?', content):
            result["url_references"].append({
                "action": ur.group(1),
                "controller": ur.group(2),
            })

        # Script and style references
        for sc in re.finditer(r'<script\s+src="([^"]+)"', content):
            result["scripts"].append(sc.group(1))
        for st in re.finditer(r'<link\s[^>]*href="([^"]+\.css[^"]*)"', content):
            result["styles"].append(st.group(1))

        # Find which controller renders this view
        controllers_rendering = []
        all_files = self._scan_all_cs_files(base)
        view_name = os.path.splitext(os.path.basename(view_path))[0]
        for finfo in all_files:
            if "Controller" not in finfo["rel_path"]:
                continue
            fc = finfo["content"]
            if re.search(rf'View\s*\(\s*"?{re.escape(view_name)}"?\s*[,)]', fc):
                ctrl_m = re.search(r"class\s+(\w+Controller)", fc)
                if ctrl_m:
                    controllers_rendering.append(ctrl_m.group(1))
        result["rendered_by"] = controllers_rendering

        return {"status": "ok", **result}

    # ── 5. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Endpoint -> middleware pipeline -> controller -> service ->
        repository -> DbContext -> entity.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        flows: List[Dict[str, Any]] = []

        # Find the controller or entry point
        target_content = None
        target_file = None
        target_class = None

        for finfo in all_files:
            content = finfo["content"]
            # Match controller by name
            if re.search(rf"class\s+{re.escape(entry_point)}\s*:", content):
                target_content = content
                target_file = finfo["rel_path"]
                target_class = entry_point
                break
            # Match by route
            if re.search(rf'Route\s*\(\s*"[^"]*{re.escape(entry_point)}[^"]*"', content):
                ctrl_m = re.search(r"class\s+(\w+Controller)", content)
                if ctrl_m:
                    target_content = content
                    target_file = finfo["rel_path"]
                    target_class = ctrl_m.group(1)
                    break

        if not target_content:
            return {"status": "error", "message": f"Entry point not found: {entry_point}"}

        # Find methods in the controller
        method_pattern = re.compile(
            r"((?:\[[\w\s\(\)=\",\.]+\]\s*)*)"
            r"public\s+(?:async\s+)?(?:Task\s*<\s*)?([\w<>]+)\s*>?\s+(\w+)\s*\(([^)]*)\)\s*\{",
            re.MULTILINE,
        )

        for mm in method_pattern.finditer(target_content):
            attrs = mm.group(1)
            return_type = mm.group(2)
            method_name = mm.group(3)
            params = mm.group(4)

            if method and method_name.lower() != method.lower():
                continue

            # Skip non-action methods
            if not re.search(r"\[Http(?:Get|Post|Put|Delete|Patch)", attrs):
                if method is None:
                    continue

            brace_start = target_content.find("{", mm.end() - 1)
            body = self._extract_brace_block(target_content, brace_start)

            flow: Dict[str, Any] = {
                "controller": target_class,
                "action": method_name,
                "file": target_file,
                "return_type": return_type,
                "steps": [],
            }

            # Extract HTTP method from attributes
            http_m = re.search(r"\[Http(\w+)", attrs)
            if http_m:
                flow["http_method"] = http_m.group(1).upper()

            # Step 1: Middleware (from Program.cs/Startup.cs)
            middleware_list = self._get_middleware_pipeline(base)
            if middleware_list:
                flow["steps"].append({
                    "type": "middleware_pipeline",
                    "middleware": middleware_list[:10],
                })

            # Step 2: Authorization
            auth_m = re.search(r"\[Authorize", attrs)
            if auth_m:
                flow["steps"].append({
                    "type": "authorization",
                    "attribute": attrs.strip()[:100],
                })

            # Step 3: Model binding / validation
            if "[FromBody]" in params or "ModelState" in body:
                flow["steps"].append({
                    "type": "model_binding",
                    "parameters": params.strip()[:200],
                    "validates_model_state": "ModelState" in body,
                })

            # Step 4: Service calls
            service_calls = re.findall(
                r"(?:_(\w+Service)\.\w+|(?:await\s+)?_(\w+)\.(\w+)\s*\()",
                body,
            )
            for sc in service_calls:
                svc_name = sc[0] or sc[1]
                svc_method = sc[2] if sc[2] else None
                flow["steps"].append({
                    "type": "service_call",
                    "service": svc_name,
                    "method": svc_method,
                })

            # Step 5: Repository / DbContext calls
            db_calls = re.findall(
                r"(?:_(?:context|db|dbContext|repository)\.([\w<>]+)\.(\w+)\s*\("
                r"|_(\w+Repository)\.(\w+)\s*\()",
                body,
            )
            for dc in db_calls:
                if dc[0]:
                    flow["steps"].append({
                        "type": "data_access",
                        "entity": dc[0],
                        "operation": dc[1],
                    })
                elif dc[2]:
                    flow["steps"].append({
                        "type": "repository_call",
                        "repository": dc[2],
                        "method": dc[3],
                    })

            # Step 6: Response
            return_match = re.findall(
                r"return\s+(Ok|Created|BadRequest|NotFound|NoContent|"
                r"Unauthorized|Forbid|StatusCode|View|RedirectToAction|"
                r"Json|File|Content)\s*\(",
                body,
            )
            if return_match:
                flow["steps"].append({
                    "type": "response",
                    "response_type": return_match[-1] if return_match else return_type,
                })

            flows.append(flow)

        return {
            "status": "ok",
            "entry_point": entry_point,
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 6. get_middleware_chain ─────────────────────────────────────────

    def get_middleware_chain(self, endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Program.cs/Startup.cs: app.UseXxx() pipeline,
        [Authorize]/[AllowAnonymous], policy-based auth.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        pipeline = self._get_middleware_pipeline(base)
        auth_policies = self._get_auth_policies(base)
        endpoint_auth: Dict[str, Any] = {}

        # If endpoint specified, find auth info for that endpoint
        if endpoint:
            all_files = self._scan_all_cs_files(base)
            for finfo in all_files:
                content = finfo["content"]
                # Find route matching endpoint
                if re.search(rf'Route\s*\([^)]*{re.escape(endpoint)}', content) or \
                   re.search(rf'Http\w+\s*\([^)]*{re.escape(endpoint)}', content):
                    # Check auth attributes
                    auth_attrs = re.findall(
                        r'\[Authorize(?:\s*\([^)]*\))?\]|\[AllowAnonymous\]',
                        content,
                    )
                    endpoint_auth = {
                        "file": finfo["rel_path"],
                        "auth_attributes": auth_attrs,
                    }

        return {
            "status": "ok",
            "pipeline": pipeline,
            "auth_policies": auth_policies,
            "endpoint_auth": endpoint_auth,
            "pipeline_order_note": "Middleware executes in registration order (top to bottom)",
        }

    def _get_middleware_pipeline(self, base: str) -> List[Dict[str, Any]]:
        """Extract app.UseXxx() middleware pipeline from startup files."""
        startup_files = self._find_startup_files(base)
        pipeline: List[Dict[str, Any]] = []

        use_pattern = re.compile(r"app\.(Use\w+)\s*\(([^)]*)\)")

        for finfo in startup_files:
            content = finfo["content"]
            for um in use_pattern.finditer(content):
                middleware_name = um.group(1)
                args = um.group(2).strip()
                entry: Dict[str, Any] = {
                    "middleware": middleware_name,
                    "file": finfo["rel_path"],
                }
                if args:
                    entry["arguments"] = args[:100]
                pipeline.append(entry)

        return pipeline

    def _get_auth_policies(self, base: str) -> List[Dict[str, Any]]:
        """Extract authorization policies from startup configuration."""
        startup_files = self._find_startup_files(base)
        policies: List[Dict[str, Any]] = []

        policy_pattern = re.compile(
            r'\.AddPolicy\s*\(\s*"(\w+)"\s*,\s*(?:policy\s*=>)?\s*'
            r'(?:policy\.)?(\w+)\s*\(',
        )

        for finfo in startup_files:
            content = finfo["content"]
            for pm in policy_pattern.finditer(content):
                policies.append({
                    "name": pm.group(1),
                    "requirement": pm.group(2),
                })

        return policies

    # ── 7. get_dependency_injection ────────────────────────────────────

    def get_dependency_injection(self, service_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse services.AddScoped/AddTransient/AddSingleton in Program.cs.
        Build DI graph, detect missing registrations.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        startup_files = self._find_startup_files(base)
        all_files = self._scan_all_cs_files(base)

        registrations: List[Dict[str, Any]] = []
        constructor_deps: Dict[str, List[Dict[str, str]]] = {}
        missing_registrations: List[str] = []

        # DI registration patterns
        di_patterns = [
            (re.compile(r"services\.Add(Scoped|Transient|Singleton)\s*<\s*(\w+)\s*(?:,\s*(\w+)\s*)?>"), "explicit"),
            (re.compile(r"services\.Add(Scoped|Transient|Singleton)\s*\(\s*typeof\s*\(\s*(\w+)\s*\)\s*(?:,\s*typeof\s*\(\s*(\w+)\s*\))?\s*\)"), "typeof"),
            (re.compile(r"builder\.Services\.Add(Scoped|Transient|Singleton)\s*<\s*(\w+)\s*(?:,\s*(\w+)\s*)?>"), "builder"),
        ]

        registered_interfaces: Set[str] = set()
        registered_implementations: Set[str] = set()

        for finfo in startup_files:
            content = finfo["content"]
            for pattern, style in di_patterns:
                for dm in pattern.finditer(content):
                    lifetime = dm.group(1)
                    interface_or_impl = dm.group(2)
                    implementation = dm.group(3)

                    reg: Dict[str, Any] = {
                        "lifetime": lifetime.lower(),
                        "file": finfo["rel_path"],
                    }

                    if implementation:
                        reg["interface"] = interface_or_impl
                        reg["implementation"] = implementation
                        registered_interfaces.add(interface_or_impl)
                        registered_implementations.add(implementation)
                    else:
                        reg["implementation"] = interface_or_impl
                        registered_implementations.add(interface_or_impl)

                    if service_name and service_name.lower() not in \
                       (interface_or_impl.lower(), (implementation or "").lower()):
                        continue

                    registrations.append(reg)

        # Parse constructor injection to build dependency graph
        ctor_pattern = re.compile(
            r"public\s+(\w+)\s*\(([^)]+)\)",
        )

        for finfo in all_files:
            content = finfo["content"]
            # Find class definitions
            class_matches = re.finditer(
                r"public\s+class\s+(\w+)\s*(?::\s*[\w\s,<>]+)?\s*\{",
                content,
            )
            for cm in class_matches:
                class_name = cm.group(1)
                brace_start = content.find("{", cm.start())
                block = self._extract_brace_block(content, brace_start)

                for ctm in ctor_pattern.finditer(block):
                    ctor_class = ctm.group(1)
                    if ctor_class != class_name:
                        continue
                    params = ctm.group(2)
                    deps = []
                    for param in params.split(","):
                        param = param.strip()
                        parts = param.split()
                        if len(parts) >= 2:
                            dep_type = parts[0]
                            dep_name = parts[1]
                            deps.append({
                                "type": dep_type,
                                "name": dep_name,
                            })
                            # Check if this dependency is registered
                            if dep_type.startswith("I") and dep_type not in registered_interfaces \
                               and dep_type not in registered_implementations \
                               and dep_type not in ("ILogger", "IOptions", "IConfiguration",
                                                     "IWebHostEnvironment", "IHostEnvironment",
                                                     "IMemoryCache", "IDistributedCache",
                                                     "IHttpClientFactory", "IMapper"):
                                if dep_type not in missing_registrations:
                                    missing_registrations.append(dep_type)

                    if deps:
                        constructor_deps[class_name] = deps

        # Filter by service_name if specified
        if service_name:
            filtered_deps = {}
            if service_name in constructor_deps:
                filtered_deps[service_name] = constructor_deps[service_name]
            # Also find who depends on this service
            dependents = {}
            for cls, deps in constructor_deps.items():
                for d in deps:
                    if d["type"] == service_name or d["type"] == f"I{service_name}":
                        dependents[cls] = deps
            filtered_deps.update(dependents)
            constructor_deps = filtered_deps

        return {
            "status": "ok",
            "registrations": registrations,
            "constructor_dependencies": constructor_deps,
            "missing_registrations": missing_registrations,
            "total_registrations": len(registrations),
            "warnings": [
                f"Potentially unregistered service: {m}" for m in missing_registrations
            ],
        }

    # ── 8. get_validation_chain ────────────────────────────────────────

    def get_validation_chain(self, controller: str, action: str) -> Dict[str, Any]:
        """
        Trace: [FromBody] DTO -> DataAnnotations ([Required]/[StringLength]/[Range])
        -> ModelState.IsValid -> response.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        result: Dict[str, Any] = {
            "controller": controller,
            "action": action,
            "steps": [],
            "dto": None,
            "validations": [],
            "model_state_check": False,
            "warnings": [],
        }

        # Find the controller
        for finfo in all_files:
            content = finfo["content"]
            if not re.search(rf"class\s+{re.escape(controller)}\s*:", content):
                continue

            # Find the action method
            action_pattern = re.compile(
                rf"((?:\[[\w\s\(\)=\",\.]+\]\s*)*)"
                rf"public\s+(?:async\s+)?(?:Task\s*<\s*)?\w+[>\s]+{re.escape(action)}\s*\(([^)]*)\)\s*\{{",
                re.MULTILINE,
            )

            am = action_pattern.search(content)
            if not am:
                result["warnings"].append(f"Action '{action}' not found in {controller}")
                break

            attrs = am.group(1)
            params = am.group(2)
            brace_start = content.find("{", am.end() - 1)
            body = self._extract_brace_block(content, brace_start)

            result["steps"].append({
                "type": "endpoint",
                "attributes": attrs.strip()[:200],
                "parameters": params.strip(),
            })

            # Find DTO type from [FromBody] parameter
            dto_match = re.search(
                r"\[FromBody\]\s*([\w<>]+)\s+(\w+)",
                params,
            )
            if not dto_match:
                # Try without [FromBody] -- complex types are bound from body by default in ApiController
                dto_match = re.search(r"([\w<>]+)\s+(\w+)", params)

            if dto_match:
                dto_type = dto_match.group(1)
                dto_param = dto_match.group(2)
                result["dto"] = {"type": dto_type, "parameter": dto_param}
                result["steps"].append({
                    "type": "model_binding",
                    "dto_type": dto_type,
                    "parameter_name": dto_param,
                })

                # Find DTO class and its DataAnnotation validations
                for df in all_files:
                    dc = df["content"]
                    dto_class_match = re.search(
                        rf"public\s+class\s+{re.escape(dto_type)}\s*(?::\s*[\w\s,<>]+)?\s*\{{",
                        dc,
                    )
                    if not dto_class_match:
                        continue

                    dto_brace = dc.find("{", dto_class_match.start())
                    dto_block = self._extract_brace_block(dc, dto_brace)

                    # Parse validation attributes
                    val_prop_pattern = re.compile(
                        r"((?:\[[\w\s\(\)=\",\.]+\]\s*)*)"
                        r"public\s+([\w<>\?]+)\s+(\w+)\s*\{",
                        re.MULTILINE,
                    )
                    for vp in val_prop_pattern.finditer(dto_block):
                        prop_attrs = vp.group(1)
                        prop_type = vp.group(2)
                        prop_name = vp.group(3)

                        validations = []
                        if re.search(r"\[Required", prop_attrs):
                            validations.append("Required")
                        sl = re.search(r"\[StringLength\s*\(\s*(\d+)", prop_attrs)
                        if sl:
                            validations.append(f"StringLength({sl.group(1)})")
                        rng = re.search(r"\[Range\s*\(\s*([^)]+)\)", prop_attrs)
                        if rng:
                            validations.append(f"Range({rng.group(1)})")
                        email = re.search(r"\[EmailAddress\]", prop_attrs)
                        if email:
                            validations.append("EmailAddress")
                        regex_v = re.search(r'\[RegularExpression\s*\(\s*"([^"]+)"', prop_attrs)
                        if regex_v:
                            validations.append(f"RegularExpression({regex_v.group(1)[:50]})")
                        compare = re.search(r'\[Compare\s*\(\s*"(\w+)"', prop_attrs)
                        if compare:
                            validations.append(f"Compare({compare.group(1)})")

                        if validations:
                            result["validations"].append({
                                "property": prop_name,
                                "type": prop_type,
                                "rules": validations,
                            })

                    result["steps"].append({
                        "type": "dto_validation",
                        "dto_type": dto_type,
                        "dto_file": df["rel_path"],
                        "validation_count": len(result["validations"]),
                    })
                    break

            # Check ModelState.IsValid
            if "ModelState.IsValid" in body or "ModelState" in body:
                result["model_state_check"] = True
                result["steps"].append({
                    "type": "model_state_check",
                    "explicit_check": "ModelState.IsValid" in body,
                })
            elif result["dto"] and "[ApiController]" not in content:
                result["warnings"].append(
                    "No ModelState.IsValid check found and [ApiController] is not present"
                )

            # Response
            return_matches = re.findall(
                r"return\s+(Ok|Created|BadRequest|NotFound|NoContent|"
                r"ValidationProblem|Problem)\s*\(",
                body,
            )
            if return_matches:
                result["steps"].append({
                    "type": "response",
                    "possible_responses": list(set(return_matches)),
                })

            break

        return {"status": "ok", **result}

    # ── 9. verify_endpoint ─────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check route registration, controller action, auth, model binding.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        checks: List[Dict[str, Any]] = []
        overall_status = "pass"

        # Get endpoint map
        endpoint_map = self.get_endpoint_map()
        if endpoint_map.get("status") != "ok":
            return {"status": "error", "message": "Could not parse endpoints"}

        # Find matching endpoint
        matched = None
        for ep in endpoint_map.get("endpoints", []):
            if ep["http_method"] == method.upper():
                # Normalise routes for comparison
                ep_route = re.sub(r"\{[^}]+\}", "{id}", ep["route"])
                check_url = re.sub(r"\{[^}]+\}", "{id}", url)
                if ep_route == check_url or ep["route"] == url:
                    matched = ep
                    break

        if matched:
            checks.append({
                "check": "route_exists",
                "status": "pass",
                "detail": f"Route {method} {url} maps to {matched['controller']}.{matched['action']}",
            })

            # Check authorization
            if matched.get("authorization"):
                checks.append({
                    "check": "authorization",
                    "status": "pass",
                    "detail": f"Endpoint has authorization: {matched['authorization']}",
                })
            elif not matched.get("allow_anonymous"):
                checks.append({
                    "check": "authorization",
                    "status": "warning",
                    "detail": "No explicit [Authorize] or [AllowAnonymous] on endpoint",
                })
                if overall_status == "pass":
                    overall_status = "warning"

            # Check model binding for POST/PUT/PATCH
            if method.upper() in ("POST", "PUT", "PATCH"):
                has_body_param = any(
                    p.get("binding") == "FromBody" for p in matched.get("parameters", [])
                )
                if has_body_param:
                    checks.append({
                        "check": "model_binding",
                        "status": "pass",
                        "detail": "Has [FromBody] parameter for request body",
                    })
                elif matched.get("api_controller"):
                    checks.append({
                        "check": "model_binding",
                        "status": "pass",
                        "detail": "[ApiController] provides implicit [FromBody] binding",
                    })
                else:
                    checks.append({
                        "check": "model_binding",
                        "status": "warning",
                        "detail": "No [FromBody] parameter found for write endpoint",
                    })
        else:
            checks.append({
                "check": "route_exists",
                "status": "fail",
                "detail": f"No route found for {method} {url}",
            })
            overall_status = "fail"

        # Check middleware pipeline
        pipeline = self._get_middleware_pipeline(base)
        has_auth_middleware = any("Auth" in m["middleware"] for m in pipeline)
        has_cors = any("Cors" in m["middleware"] for m in pipeline)

        checks.append({
            "check": "middleware",
            "status": "pass" if has_auth_middleware else "warning",
            "detail": f"Auth middleware: {'present' if has_auth_middleware else 'not found'}, "
                      f"CORS: {'present' if has_cors else 'not found'}",
        })

        return {
            "status": "ok",
            "method": method,
            "url": url,
            "overall": overall_status,
            "checks": checks,
            "matched_endpoint": matched,
        }

    # ── 10. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Patterns: naming, di, middleware, testing, configuration.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}

        if pattern_type == "naming":
            # Analyse naming conventions
            class_names = []
            method_names = []
            for finfo in all_files:
                content = finfo["content"]
                for cm in re.finditer(r"public\s+class\s+(\w+)", content):
                    class_names.append(cm.group(1))
                for mm in re.finditer(r"public\s+(?:async\s+)?[\w<>]+\s+(\w+)\s*\(", content):
                    method_names.append(mm.group(1))

            # Detect naming patterns
            suffixes: Dict[str, int] = {}
            for name in class_names:
                for suffix in ["Controller", "Service", "Repository", "Handler",
                               "Validator", "Mapper", "Factory", "Builder", "Dto",
                               "ViewModel", "Response", "Request", "Command", "Query"]:
                    if name.endswith(suffix):
                        suffixes[suffix] = suffixes.get(suffix, 0) + 1

            conventions["findings"] = [{
                "convention": "class_suffixes",
                "detail": suffixes,
                "total_classes": len(class_names),
            }]

            # Check PascalCase compliance
            pascal_compliant = sum(1 for n in class_names if n[0:1].isupper())
            conventions["findings"].append({
                "convention": "pascal_case_classes",
                "compliant": pascal_compliant,
                "total": len(class_names),
                "percentage": round(pascal_compliant / len(class_names) * 100, 1) if class_names else 0,
            })

        elif pattern_type == "di":
            # DI registration patterns
            di_result = self.get_dependency_injection()
            registrations = di_result.get("registrations", [])
            lifetime_counts = {"scoped": 0, "transient": 0, "singleton": 0}
            for reg in registrations:
                lt = reg.get("lifetime", "")
                if lt in lifetime_counts:
                    lifetime_counts[lt] += 1

            conventions["findings"] = [{
                "convention": "di_lifetime_distribution",
                "lifetimes": lifetime_counts,
                "total": len(registrations),
            }]

            # Check interface-based registration
            with_interface = sum(1 for r in registrations if r.get("interface"))
            conventions["findings"].append({
                "convention": "interface_registration",
                "with_interface": with_interface,
                "without_interface": len(registrations) - with_interface,
            })

        elif pattern_type == "middleware":
            pipeline = self._get_middleware_pipeline(base)
            conventions["findings"] = [{
                "convention": "middleware_pipeline",
                "pipeline": pipeline,
                "total": len(pipeline),
            }]

        elif pattern_type == "testing":
            test_files = self._scan_files(base, "Tests") or \
                         self._scan_files(base, "tests") or \
                         self._scan_files(base, "Test")
            test_frameworks = set()
            test_patterns_found: Dict[str, int] = {}

            for finfo in test_files:
                content = finfo["content"]
                if "[Fact]" in content or "[Theory]" in content:
                    test_frameworks.add("xUnit")
                if "[Test]" in content or "[TestCase]" in content:
                    test_frameworks.add("NUnit")
                if "[TestMethod]" in content:
                    test_frameworks.add("MSTest")
                if "Mock<" in content or "new Mock" in content:
                    test_patterns_found["Moq"] = test_patterns_found.get("Moq", 0) + 1
                if "Substitute.For" in content:
                    test_patterns_found["NSubstitute"] = test_patterns_found.get("NSubstitute", 0) + 1
                if "WebApplicationFactory" in content:
                    test_patterns_found["IntegrationTest"] = test_patterns_found.get("IntegrationTest", 0) + 1

            conventions["findings"] = [{
                "convention": "test_frameworks",
                "frameworks": list(test_frameworks),
                "patterns": test_patterns_found,
                "test_file_count": len(test_files),
            }]

        elif pattern_type == "configuration":
            # Parse appsettings.json
            config_files = []
            for fname in ["appsettings.json", "appsettings.Development.json",
                          "appsettings.Production.json"]:
                fpath = os.path.join(base, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            config_files.append({
                                "file": fname,
                                "size": os.path.getsize(fpath),
                            })
                    except Exception:
                        pass

            # Find IOptions<T> usage
            options_types = set()
            for finfo in all_files:
                for om in re.finditer(r"IOptions(?:Monitor|Snapshot)?\s*<\s*(\w+)\s*>", finfo["content"]):
                    options_types.add(om.group(1))

            conventions["findings"] = [{
                "convention": "configuration_files",
                "files": config_files,
                "options_types": list(options_types),
            }]

        return {"status": "ok", **conventions}

    # ── 11. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Registry: middleware, filter, health-check, background-service,
        signalr-hub, grpc-service, rate-limiting.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        desc_lower = description.lower()

        # Find matching pattern types
        matched_types = []
        for ptype, pinfo in self.PATTERN_REGISTRY.items():
            if any(word in desc_lower for word in ptype.split("-")):
                matched_types.append(ptype)

        # If no specific match, search all patterns
        if not matched_types:
            matched_types = list(self.PATTERN_REGISTRY.keys())

        results: List[Dict[str, Any]] = []

        for ptype in matched_types:
            pinfo = self.PATTERN_REGISTRY[ptype]
            matches = []
            for finfo in all_files:
                content = finfo["content"]
                for pattern_str in pinfo["patterns"]:
                    for pm in re.finditer(pattern_str, content):
                        line_num = content[:pm.start()].count("\n") + 1
                        line_text = content.split("\n")[line_num - 1].strip()
                        matches.append({
                            "file": finfo["rel_path"],
                            "line": line_num,
                            "text": line_text[:120],
                        })
                        if len(matches) >= 10:
                            break
                    if len(matches) >= 10:
                        break

            if matches:
                results.append({
                    "pattern_type": ptype,
                    "description": pinfo["description"],
                    "matches": matches[:10],
                    "total": len(matches),
                })

        # Also do a keyword search in class names and method names
        keyword_matches = []
        keywords = desc_lower.split()
        for finfo in all_files:
            content = finfo["content"]
            for kw in keywords:
                if len(kw) < 3:
                    continue
                for cm in re.finditer(
                    rf"(?:class|interface|struct)\s+(\w*{re.escape(kw)}\w*)",
                    content, re.IGNORECASE,
                ):
                    keyword_matches.append({
                        "file": finfo["rel_path"],
                        "symbol": cm.group(1),
                        "type": "class/interface",
                    })
                    if len(keyword_matches) >= 15:
                        break

        return {
            "status": "ok",
            "query": description,
            "pattern_matches": results,
            "keyword_matches": keyword_matches[:15],
        }

    # ── 12. get_project_snapshot ───────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview: controllers, entities, services, middleware pipeline,
        DI registrations, configuration.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        startup_files = self._find_startup_files(base)

        controllers: List[str] = []
        entities: List[str] = []
        services: List[str] = []
        repositories: List[str] = []
        dtos: List[str] = []
        test_count = 0

        for finfo in all_files:
            content = finfo["content"]
            for cm in re.finditer(r"public\s+class\s+(\w+)", content):
                name = cm.group(1)
                if name.endswith("Controller"):
                    controllers.append(name)
                elif name.endswith("Service") and not name.startswith("I"):
                    services.append(name)
                elif name.endswith("Repository") and not name.startswith("I"):
                    repositories.append(name)
                elif name.endswith("Dto") or name.endswith("DTO") or \
                     name.endswith("ViewModel") or name.endswith("Request") or \
                     name.endswith("Response"):
                    dtos.append(name)

            # Count test methods
            test_count += len(re.findall(r"\[(?:Fact|Test|TestMethod|Theory)\]", content))

            # Check for entities (DbSet references)
            for dbm in re.finditer(r"DbSet\s*<\s*(\w+)\s*>", content):
                ent = dbm.group(1)
                if ent not in entities:
                    entities.append(ent)

        # Middleware pipeline
        pipeline = self._get_middleware_pipeline(base)

        # DI registrations count
        di_result = self.get_dependency_injection()
        di_count = di_result.get("total_registrations", 0)

        # Endpoint count
        endpoint_result = self.get_endpoint_map()
        endpoint_count = endpoint_result.get("total", 0)

        # Project files
        csproj_files = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("bin", "obj", ".git", "node_modules")]
            for f in files:
                if f.endswith(".csproj"):
                    csproj_files.append(os.path.relpath(os.path.join(root, f), base))

        return {
            "status": "ok",
            "project_files": csproj_files,
            "controllers": sorted(controllers),
            "entities": sorted(entities),
            "services": sorted(services),
            "repositories": sorted(repositories),
            "dtos": sorted(dtos)[:30],
            "middleware_pipeline": [m["middleware"] for m in pipeline],
            "di_registrations": di_count,
            "endpoint_count": endpoint_count,
            "test_count": test_count,
            "total_cs_files": len(all_files),
            "summary": {
                "controllers": len(controllers),
                "entities": len(entities),
                "services": len(services),
                "repositories": len(repositories),
                "dtos": len(dtos),
            },
        }

    # ── 13. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit checks for ASP.NET Core projects.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_cs_files(base)
        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name,
            "references": [],
            "impact": [],
            "warnings": [],
        }

        # Find all references to the symbol
        for finfo in all_files:
            content = finfo["content"]
            if symbol_name not in content:
                continue
            lines = content.split("\n")
            refs = []
            for i, line in enumerate(lines, 1):
                if symbol_name in line:
                    refs.append({
                        "line": i,
                        "text": line.strip()[:120],
                    })
            if refs:
                result["references"].append({
                    "file": finfo["rel_path"],
                    "usages": refs[:10],
                    "count": len(refs),
                })

        # Context-aware checks based on file type
        if "Controller" in file_path:
            # Check routes, views, DI
            result["impact"].append({
                "type": "route_check",
                "detail": "Verify route still resolves after edit",
            })
            result["impact"].append({
                "type": "view_check",
                "detail": "Check Razor views that depend on this controller",
            })

        elif "Model" in file_path or "Entity" in file_path or "Data" in file_path:
            # Entity change: check migrations, DbContext, services
            result["impact"].append({
                "type": "migration_needed",
                "detail": "Entity change may require a new EF Core migration",
            })
            result["impact"].append({
                "type": "dbcontext_check",
                "detail": "Verify DbContext configuration is consistent",
            })
            result["impact"].append({
                "type": "dto_check",
                "detail": "Check DTOs/ViewModels that map to this entity",
            })

        elif "Service" in file_path:
            result["impact"].append({
                "type": "di_check",
                "detail": "Verify DI registration is still valid",
            })
            result["impact"].append({
                "type": "controller_check",
                "detail": "Check controllers that inject this service",
            })

        elif "Middleware" in file_path:
            result["impact"].append({
                "type": "pipeline_check",
                "detail": "Verify middleware pipeline order in Program.cs",
            })

        # Anti-pattern check
        full_path = os.path.join(base, file_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                for ap in self.ANTI_PATTERNS:
                    if re.search(ap["pattern"], content):
                        result["warnings"].append({
                            "severity": ap["severity"],
                            "message": ap["message"],
                        })
            except Exception:
                pass

        result["total_references"] = sum(r["count"] for r in result["references"])
        result["files_affected"] = len(result["references"])

        return {"status": "ok", **result}
