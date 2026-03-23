"""
Rust Web Intelligence Service - Rust-specific code analysis for Actix/Axum/Rocket.

Provides struct/impl/trait mapping, multi-framework route parsing,
Diesel/SQLx/SeaORM schema extraction, middleware/layer resolution,
error handling analysis, and Rust-specific flow tracing.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class RustIntelligenceService(BaseService):
    """Rust-specific code intelligence for Actix-web/Axum/Rocket projects."""

    SCAN_DIRS = {
        "handlers": "src/handlers",
        "models": "src/models",
        "services": "src/services",
        "middleware": "src/middleware",
        "routes": "src/routes",
        "schema": "src/schema",
        "errors": "src/errors",
        "tests": "tests",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\.unwrap\s*\(\s*\)",
            "severity": "warning",
            "message": ".unwrap() in production code -- use ? operator or proper error handling",
            "file_types": ["rs"],
        },
        {
            "pattern": r"\bprintln!\s*\(",
            "severity": "warning",
            "message": "println! in production code -- use tracing or log crate instead",
            "file_types": ["rs"],
        },
        {
            "pattern": r"\.clone\s*\(\s*\)",
            "severity": "info",
            "message": ".clone() detected -- verify this clone is necessary",
            "file_types": ["rs"],
        },
        {
            "pattern": r"\bunsafe\s*\{",
            "severity": "warning",
            "message": "unsafe block -- ensure it has a SAFETY comment documenting invariants",
            "file_types": ["rs"],
        },
    ]

    PATTERN_REGISTRY = {
        "middleware": {
            "patterns": [
                r"\.wrap\s*\(",
                r"\.layer\s*\(",
                r"impl\s+\w+\s+for\s+\w+Middleware",
                r"fn\s+\w+_middleware",
                r"ServiceBuilder::new\(\)\.layer",
            ],
            "description": "HTTP middleware / Tower layer patterns",
        },
        "extractor": {
            "patterns": [
                r"impl\s+FromRequest\s+for",
                r"impl\s+FromRequestParts\s+for",
                r"web::(Json|Path|Query|Data|Form)\s*<",
                r"extract::(Json|Path|Query|State)\s*<",
            ],
            "description": "Request extractor patterns (Actix/Axum)",
        },
        "error-handler": {
            "patterns": [
                r"impl\s+ResponseError\s+for",
                r"impl\s+IntoResponse\s+for\s+\w*Error",
                r"#\[derive\(.*thiserror",
                r"anyhow::",
            ],
            "description": "Error handling patterns (thiserror, anyhow, ResponseError)",
        },
        "auth-guard": {
            "patterns": [
                r"impl\s+Guard\s+for",
                r"impl\s+FromRequest\s+for\s+\w*Auth",
                r"fn\s+\w*auth\w*_middleware",
                r"jwt::|jsonwebtoken::",
            ],
            "description": "Authentication and authorization guard patterns",
        },
        "rate-limiter": {
            "patterns": [
                r"governor::",
                r"RateLimiter",
                r"rate_limit",
                r"throttle",
            ],
            "description": "Rate limiting middleware patterns",
        },
        "websocket": {
            "patterns": [
                r"ws::|WebSocket|websocket",
                r"actix_web_actors::ws",
                r"axum::extract::ws",
                r"tokio_tungstenite",
            ],
            "description": "WebSocket handler patterns",
        },
        "background-task": {
            "patterns": [
                r"tokio::spawn\s*\(",
                r"tokio::task::",
                r"actix_rt::spawn",
                r"#\[tokio::main\]",
            ],
            "description": "Background task / async spawn patterns",
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
                    extensions: tuple = (".rs",)) -> List[dict]:
        """Scan directory recursively for Rust files."""
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in ("target", ".git", "node_modules")]
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

    def _scan_all_rs_files(self, base: str) -> List[dict]:
        """Scan all Rust source files in the project."""
        all_files: List[dict] = []
        seen: Set[str] = set()

        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        extra_dirs = ["src", "crates", "libs", "bin", "examples"]
        for d in extra_dirs:
            for f in self._scan_files(base, d):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    def _extract_brace_block(self, content: str, start: int) -> str:
        """Extract content inside matching braces."""
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

    # ── 1. get_struct_map ──────────────────────────────────────────────

    def get_struct_map(self, struct_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse struct definitions, impl blocks, derive macros, trait implementations.
        Map struct -> impl -> trait.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        structs: Dict[str, Any] = {}

        # Struct definition with derives
        struct_pattern = re.compile(
            r"((?:#\[derive\(([^)]+)\)\]\s*)*"
            r"(?:#\[[^\]]+\]\s*)*)"
            r"pub(?:\s*\(crate\))?\s+struct\s+(\w+)\s*(?:<[^>]+>)?\s*\{",
            re.MULTILINE,
        )

        # Tuple struct
        tuple_struct_pattern = re.compile(
            r"((?:#\[derive\(([^)]+)\)\]\s*)*)"
            r"pub(?:\s*\(crate\))?\s+struct\s+(\w+)\s*(?:<[^>]+>)?\s*\(",
            re.MULTILINE,
        )

        # Impl blocks
        impl_pattern = re.compile(
            r"impl\s+(?:<[^>]+>\s*)?(\w+)\s*(?:<[^>]+>)?\s+for\s+(\w+)\s*(?:<[^>]+>)?\s*\{",
            re.MULTILINE,
        )
        inherent_impl_pattern = re.compile(
            r"impl\s+(?:<[^>]+>\s*)?(\w+)\s*(?:<[^>]+>)?\s*\{",
            re.MULTILINE,
        )

        # Field pattern
        field_pattern = re.compile(
            r"(?:pub(?:\s*\([\w,\s]+\))?\s+)?(\w+)\s*:\s*([\w<>:&'\s\[\](),]+?)(?:\s*,\s*$|\s*$)",
            re.MULTILINE,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Skip test files for struct analysis
            if "_test" in rel_path and not struct_name:
                continue

            # Parse struct definitions
            for sm in struct_pattern.finditer(content):
                attrs_block = sm.group(1)
                derives_raw = ""
                # Collect all derive macros
                for dm in re.finditer(r"#\[derive\(([^)]+)\)\]", attrs_block):
                    derives_raw += dm.group(1) + ","
                derives = [d.strip() for d in derives_raw.split(",") if d.strip()]
                name = sm.group(3)

                if struct_name and name.lower() != struct_name.lower():
                    continue

                brace_start = content.find("{", sm.start())
                block = self._extract_brace_block(content, brace_start)

                fields = []
                for line in block.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//") or line.startswith("#"):
                        continue
                    fm = re.match(
                        r"(?:pub(?:\s*\([\w,\s]+\))?\s+)?(\w+)\s*:\s*(.+?)\s*,?\s*$",
                        line,
                    )
                    if fm:
                        field_name = fm.group(1)
                        field_type = fm.group(2).strip().rstrip(",")
                        field_info: Dict[str, Any] = {
                            "name": field_name,
                            "type": field_type,
                        }

                        # Detect serde attributes
                        serde_attrs = []
                        for sa in re.finditer(r'#\[serde\(([^)]+)\)\]', line):
                            serde_attrs.append(sa.group(1))
                        if serde_attrs:
                            field_info["serde"] = serde_attrs

                        fields.append(field_info)

                # Parse other attributes on the struct
                other_attrs = []
                for attr in re.finditer(r"#\[(\w+)(?:\([^)]*\))?\]", attrs_block):
                    attr_name = attr.group(0)
                    if "derive" not in attr_name:
                        other_attrs.append(attr_name)

                structs[name] = {
                    "file": rel_path,
                    "fields": fields,
                    "derives": derives,
                    "attributes": other_attrs,
                    "impl_blocks": [],
                    "trait_implementations": [],
                }

            # Parse tuple structs
            for ts in tuple_struct_pattern.finditer(content):
                derives_raw = ts.group(2) or ""
                derives = [d.strip() for d in derives_raw.split(",") if d.strip()]
                name = ts.group(3)

                if struct_name and name.lower() != struct_name.lower():
                    continue

                if name not in structs:
                    structs[name] = {
                        "file": rel_path,
                        "fields": [],
                        "derives": derives,
                        "attributes": [],
                        "impl_blocks": [],
                        "trait_implementations": [],
                        "tuple_struct": True,
                    }

        # Second pass: find impl blocks and trait implementations
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Trait implementations: impl Trait for Struct
            for tim in impl_pattern.finditer(content):
                trait_name = tim.group(1)
                target_struct = tim.group(2)

                if struct_name and target_struct.lower() != struct_name.lower():
                    continue

                brace_start = content.find("{", tim.start())
                block = self._extract_brace_block(content, brace_start)

                # Extract method signatures
                methods = []
                for mm in re.finditer(
                    r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)"
                    r"\s*(?:->\s*([\w<>:&'\s\[\](),]+?))?\s*(?:where[^{]*)?\{",
                    block,
                ):
                    methods.append({
                        "name": mm.group(1),
                        "params": mm.group(2).strip()[:100],
                        "return_type": mm.group(3).strip() if mm.group(3) else "()",
                    })

                if target_struct in structs:
                    structs[target_struct]["trait_implementations"].append({
                        "trait": trait_name,
                        "file": rel_path,
                        "methods": methods,
                    })

            # Inherent impl blocks: impl Struct
            for iim in inherent_impl_pattern.finditer(content):
                # Make sure this is not a trait impl
                pre = content[max(0, iim.start() - 5):iim.start()]
                if "for" in content[iim.start():iim.end()]:
                    continue

                target_struct = iim.group(1)

                if struct_name and target_struct.lower() != struct_name.lower():
                    continue

                brace_start = content.find("{", iim.start())
                block = self._extract_brace_block(content, brace_start)

                methods = []
                for mm in re.finditer(
                    r"(?:pub(?:\s*\([\w,\s]+\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*"
                    r"(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?:->\s*([\w<>:&'\s\[\](),!]+?))?\s*"
                    r"(?:where[^{]*)?\{",
                    block,
                ):
                    methods.append({
                        "name": mm.group(1),
                        "params": mm.group(2).strip()[:100],
                        "return_type": mm.group(3).strip() if mm.group(3) else "()",
                    })

                if target_struct in structs:
                    structs[target_struct]["impl_blocks"].append({
                        "file": rel_path,
                        "methods": methods,
                    })

        return {
            "status": "ok",
            "structs": structs,
            "total_structs": len(structs),
        }

    # ── 2. get_route_map ───────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Actix (#[get("/path")]), Axum (Router::new().route()),
        Rocket (#[get("/")]). Extract routes, handlers, extractors.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        routes: List[Dict[str, Any]] = []
        framework = "unknown"

        # Actix-web route attribute macros
        actix_route_pattern = re.compile(
            r'#\[(get|post|put|delete|patch|head)\s*\(\s*"([^"]+)"'
            r'(?:\s*,\s*guard\s*=\s*"([^"]+)")?\s*\)\]\s*'
            r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)'
            r'\s*(?:->\s*([\w<>:&\'\s\[\](),!]+?))?\s*\{',
            re.MULTILINE | re.DOTALL,
        )

        # Actix web::resource/web::scope routes
        actix_resource_pattern = re.compile(
            r'web::(?:resource|scope)\s*\(\s*"([^"]+)"\s*\)'
            r'(?:\.route\s*\(\s*web::(get|post|put|delete|patch)\s*\(\)\s*\.to\s*\(\s*(\w+)\s*\))?',
        )

        # Axum Router routes
        axum_route_pattern = re.compile(
            r'\.route\s*\(\s*"([^"]+)"\s*,\s*'
            r'(get|post|put|delete|patch|head)\s*\(\s*(\w+)\s*\)',
        )

        # Axum method_router chain: get(handler).post(handler)
        axum_method_router = re.compile(
            r'\.route\s*\(\s*"([^"]+)"\s*,\s*'
            r'((?:(?:get|post|put|delete|patch)\s*\(\s*\w+\s*\)\s*\.?\s*)+)',
        )

        # Rocket attribute macros
        rocket_route_pattern = re.compile(
            r'#\[(get|post|put|delete|patch|head)\s*\(\s*"([^"]+)"'
            r'(?:[^)]*)\)\]\s*'
            r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)'
            r'\s*(?:->\s*([\w<>:&\'\s\[\](),!]+?))?\s*\{',
            re.MULTILINE | re.DOTALL,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Try Actix-web attribute routes
            for rm in actix_route_pattern.finditer(content):
                framework = "actix-web"
                method = rm.group(1).upper()
                path = rm.group(2)
                guard = rm.group(3)
                handler = rm.group(4)
                params_raw = rm.group(5)
                return_type = rm.group(6) or "impl Responder"

                if filter_prefix and not path.startswith(filter_prefix):
                    continue

                extractors = self._parse_rust_extractors(params_raw)

                route_info: Dict[str, Any] = {
                    "route": path,
                    "http_method": method,
                    "handler": handler,
                    "return_type": return_type.strip(),
                    "extractors": extractors,
                    "file": rel_path,
                    "framework": "actix-web",
                }
                if guard:
                    route_info["guard"] = guard
                routes.append(route_info)

            # Actix resource/scope
            for rm in actix_resource_pattern.finditer(content):
                framework = "actix-web"
                path = rm.group(1)
                method = rm.group(2)
                handler = rm.group(3)
                if method and handler:
                    if filter_prefix and not path.startswith(filter_prefix):
                        continue
                    routes.append({
                        "route": path,
                        "http_method": method.upper() if method else "GET",
                        "handler": handler or "unknown",
                        "file": rel_path,
                        "framework": "actix-web",
                        "programmatic": True,
                    })

            # Try Axum routes
            for rm in axum_route_pattern.finditer(content):
                framework = "axum"
                path = rm.group(1)
                method = rm.group(2).upper()
                handler = rm.group(3)

                if filter_prefix and not path.startswith(filter_prefix):
                    continue

                routes.append({
                    "route": path,
                    "http_method": method,
                    "handler": handler,
                    "file": rel_path,
                    "framework": "axum",
                })

            # Axum chained method routers
            for rm in axum_method_router.finditer(content):
                path = rm.group(1)
                methods_block = rm.group(2)
                for mm in re.finditer(r"(get|post|put|delete|patch)\s*\(\s*(\w+)\s*\)", methods_block):
                    method = mm.group(1).upper()
                    handler = mm.group(2)
                    if filter_prefix and not path.startswith(filter_prefix):
                        continue
                    routes.append({
                        "route": path,
                        "http_method": method,
                        "handler": handler,
                        "file": rel_path,
                        "framework": "axum",
                    })

            # Try Rocket routes
            for rm in rocket_route_pattern.finditer(content):
                framework = "rocket"
                method = rm.group(1).upper()
                path = rm.group(2)
                handler = rm.group(3)
                params_raw = rm.group(4)
                return_type = rm.group(5) or "impl Responder"

                if filter_prefix and not path.startswith(filter_prefix):
                    continue

                extractors = self._parse_rust_extractors(params_raw)
                routes.append({
                    "route": path,
                    "http_method": method,
                    "handler": handler,
                    "return_type": return_type.strip(),
                    "extractors": extractors,
                    "file": rel_path,
                    "framework": "rocket",
                })

        return {
            "status": "ok",
            "framework_detected": framework,
            "routes": routes,
            "total": len(routes),
        }

    def _parse_rust_extractors(self, params: str) -> List[Dict[str, str]]:
        """Parse function parameters to identify Actix/Axum/Rocket extractors."""
        extractors = []
        for param in params.split(","):
            param = param.strip()
            if not param or param.startswith("&self") or param == "self":
                continue
            # web::Json<T>, web::Path<T>, web::Query<T>, web::Data<T>
            ext_m = re.search(r"(?:web|extract)::(\w+)\s*<\s*([^>]+)\s*>", param)
            if ext_m:
                extractors.append({
                    "extractor": ext_m.group(1),
                    "type": ext_m.group(2).strip(),
                })
                continue
            # State<T>, Json<T>, Path<T>, Query<T>
            ext_m2 = re.search(r"(\w+)\s*<\s*([^>]+)\s*>", param)
            if ext_m2:
                extractors.append({
                    "extractor": ext_m2.group(1),
                    "type": ext_m2.group(2).strip(),
                })
                continue
            # Plain type parameter
            type_m = re.search(r"(\w+)\s*:\s*(.+)", param)
            if type_m:
                extractors.append({
                    "name": type_m.group(1),
                    "type": type_m.group(2).strip(),
                })

        return extractors

    # ── 3. get_schema_info ─────────────────────────────────────────────

    def get_schema_info(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Diesel schema.rs (table! macro), SQLx queries, SeaORM entities.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        schema: Dict[str, Any] = {}
        orm_detected = "unknown"

        # Diesel table! macro
        diesel_table_pattern = re.compile(
            r"(?:diesel::)?table!\s*\{\s*(\w+)\s*\(\s*(\w+)\s*\)\s*\{([^}]+)\}",
            re.DOTALL,
        )

        # Diesel column pattern inside table!
        diesel_column = re.compile(
            r"(\w+)\s*->\s*([\w<>]+)",
        )

        # SeaORM entity patterns
        sea_entity_pattern = re.compile(
            r"#\[sea_orm\(table_name\s*=\s*\"(\w+)\"\)\]",
        )
        sea_column_pattern = re.compile(
            r"(\w+):\s*(?:ColumnType::)?(\w+)(?:\((\d+)\))?",
        )

        # SQLx FromRow derive
        sqlx_fromrow = re.compile(
            r"#\[derive\([^)]*FromRow[^)]*\)\]",
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Parse Diesel table! macros
            for tm in diesel_table_pattern.finditer(content):
                orm_detected = "diesel"
                tbl_name = tm.group(1)
                pk = tm.group(2)
                columns_block = tm.group(3)

                if table_name and table_name.lower() != tbl_name.lower():
                    continue

                columns = []
                for col in diesel_column.finditer(columns_block):
                    columns.append({
                        "name": col.group(1),
                        "diesel_type": col.group(2),
                    })

                schema[tbl_name] = {
                    "source": "diesel",
                    "file": rel_path,
                    "primary_key": pk,
                    "columns": columns,
                }

            # Parse SeaORM entities
            for sem in sea_entity_pattern.finditer(content):
                orm_detected = "sea-orm"
                tbl_name = sem.group(1)

                if table_name and table_name.lower() != tbl_name.lower():
                    continue

                # Find the Model struct
                model_m = re.search(
                    r"pub\s+struct\s+Model\s*\{([^}]+)\}",
                    content[sem.end():],
                )
                if model_m:
                    columns = []
                    for line in model_m.group(1).split("\n"):
                        line = line.strip()
                        if not line or line.startswith("//"):
                            continue
                        fm = re.match(r"pub\s+(\w+)\s*:\s*(.+?)\s*,?\s*$", line)
                        if fm:
                            columns.append({
                                "name": fm.group(1),
                                "rust_type": fm.group(2),
                            })

                    schema[tbl_name] = {
                        "source": "sea-orm",
                        "file": rel_path,
                        "columns": columns,
                    }

            # Parse SQLx FromRow structs
            if sqlx_fromrow.search(content):
                orm_detected = "sqlx"
                for sm in re.finditer(
                    r"#\[derive\([^)]*FromRow[^)]*\)\]\s*"
                    r"pub\s+struct\s+(\w+)\s*\{([^}]+)\}",
                    content,
                    re.DOTALL,
                ):
                    struct_name = sm.group(1)
                    # Derive table name from struct name (snake_case + plural)
                    tbl = re.sub(r"(?<!^)(?=[A-Z])", "_", struct_name).lower() + "s"

                    if table_name and table_name.lower() != tbl.lower() \
                       and table_name.lower() != struct_name.lower():
                        continue

                    fields_block = sm.group(2)
                    columns = []
                    for line in fields_block.split("\n"):
                        line = line.strip()
                        if not line or line.startswith("//"):
                            continue
                        fm = re.match(r"pub\s+(\w+)\s*:\s*(.+?)\s*,?\s*$", line)
                        if fm:
                            col_info: Dict[str, Any] = {
                                "name": fm.group(1),
                                "rust_type": fm.group(2),
                            }
                            # Check for sqlx rename attribute
                            rename = re.search(
                                rf'#\[sqlx\(rename\s*=\s*"(\w+)"\)\].*{re.escape(fm.group(1))}',
                                fields_block,
                            )
                            if rename:
                                col_info["db_column"] = rename.group(1)
                            columns.append(col_info)

                    schema[tbl] = {
                        "source": "sqlx",
                        "struct_name": struct_name,
                        "file": rel_path,
                        "columns": columns,
                    }

        return {
            "status": "ok",
            "orm_detected": orm_detected,
            "schema": schema,
            "total_tables": len(schema),
        }

    # ── 4. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> middleware/layer -> handler -> service ->
        repository -> DB query -> response.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        flows: List[Dict[str, Any]] = []

        # Find the handler function
        handler_content = None
        handler_file = None

        for finfo in all_files:
            content = finfo["content"]
            # Match by function name
            pattern = re.compile(
                rf"(?:pub\s+)?(?:async\s+)?fn\s+{re.escape(entry_point)}\s*"
                rf"(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?:->\s*([\w<>:&'\s\[\](),!]+?))?\s*\{{",
                re.MULTILINE,
            )
            fm = pattern.search(content)
            if fm:
                handler_content = content
                handler_file = finfo["rel_path"]
                break

        if not handler_content:
            return {"status": "error", "message": f"Handler not found: {entry_point}"}

        # Extract handler body
        brace_start = handler_content.find("{", fm.start())
        body = self._extract_brace_block(handler_content, brace_start)
        params = fm.group(1)
        return_type = fm.group(2) or "impl Responder"

        flow: Dict[str, Any] = {
            "handler": entry_point,
            "file": handler_file,
            "return_type": return_type.strip(),
            "steps": [],
        }

        # Extract parameters (extractors)
        extractors = self._parse_rust_extractors(params)
        if extractors:
            flow["steps"].append({
                "type": "extractors",
                "extractors": extractors,
            })

        # Service calls: service.method(), repo.method()
        service_calls = re.findall(
            r"(\w+(?:_service|_repo|_repository|_client))\s*\.\s*(\w+)\s*\(",
            body,
        )
        for svc, svc_method in service_calls:
            flow["steps"].append({
                "type": "service_call",
                "service": svc,
                "method": svc_method,
            })

        # Database calls: sqlx::query, diesel queries, sea-orm
        db_calls = re.findall(
            r"(sqlx::query(?:_as)?|diesel::\w+|\.find\(|\.filter\(|\.insert_into\(|"
            r"\.update\(|\.delete\(|\.save\(|Entity::find)",
            body,
        )
        if db_calls:
            flow["steps"].append({
                "type": "database_operations",
                "operations": list(set(db_calls)),
            })

        # Error handling: ? operator, map_err, unwrap_or
        error_handling = []
        if "?" in body:
            error_handling.append("question_mark_operator")
        if "map_err" in body:
            error_handling.append("map_err")
        if "unwrap_or" in body:
            error_handling.append("unwrap_or_fallback")
        if error_handling:
            flow["steps"].append({
                "type": "error_handling",
                "patterns": error_handling,
            })

        # Response construction
        response_patterns = re.findall(
            r"(HttpResponse::\w+|Json\s*\(|StatusCode::\w+|"
            r"Ok\s*\(|Err\s*\(|\.into_response\(\))",
            body,
        )
        if response_patterns:
            flow["steps"].append({
                "type": "response",
                "patterns": list(set(response_patterns))[:10],
            })

        flows.append(flow)

        return {
            "status": "ok",
            "entry_point": entry_point,
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 5. get_middleware_chain ─────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Actix middleware (wrap()), Axum layers (layer()), Tower middleware.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        middleware_list: List[Dict[str, Any]] = []

        # Actix wrap patterns
        actix_wrap = re.compile(r"\.wrap\s*\(\s*([^)]+)\)")
        actix_wrap_fn = re.compile(r"\.wrap_fn\s*\(\s*\|")

        # Axum layer patterns
        axum_layer = re.compile(r"\.layer\s*\(\s*([^)]+)\)")

        # Tower ServiceBuilder
        tower_layer = re.compile(r"ServiceBuilder::new\(\)\s*((?:\.layer\s*\([^)]+\)\s*)*)")

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            for wm in actix_wrap.finditer(content):
                middleware_list.append({
                    "type": "actix_wrap",
                    "middleware": wm.group(1).strip()[:100],
                    "file": rel_path,
                    "line": content[:wm.start()].count("\n") + 1,
                })

            for wfm in actix_wrap_fn.finditer(content):
                middleware_list.append({
                    "type": "actix_wrap_fn",
                    "middleware": "closure middleware",
                    "file": rel_path,
                    "line": content[:wfm.start()].count("\n") + 1,
                })

            for lm in axum_layer.finditer(content):
                middleware_list.append({
                    "type": "axum_layer",
                    "layer": lm.group(1).strip()[:100],
                    "file": rel_path,
                    "line": content[:lm.start()].count("\n") + 1,
                })

            for tm in tower_layer.finditer(content):
                layers_block = tm.group(1)
                for layer in re.finditer(r"\.layer\s*\(\s*([^)]+)\)", layers_block):
                    middleware_list.append({
                        "type": "tower_layer",
                        "layer": layer.group(1).strip()[:100],
                        "file": rel_path,
                    })

        # Custom middleware implementations
        custom_mw: List[Dict[str, Any]] = []
        for finfo in all_files:
            content = finfo["content"]
            # Actix: impl Service / Transform
            for cm in re.finditer(r"impl\s+\w+\s+for\s+(\w+)\s*.*\{", content):
                if "Transform" in content[cm.start():cm.end() + 200] or \
                   "Service" in content[cm.start():cm.end() + 200]:
                    custom_mw.append({
                        "name": cm.group(1),
                        "file": finfo["rel_path"],
                    })

        return {
            "status": "ok",
            "middleware": middleware_list,
            "custom_middleware": custom_mw,
            "total": len(middleware_list),
        }

    # ── 6. get_trait_implementations ───────────────────────────────────

    def get_trait_implementations(self, trait_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Find all `impl TraitName for Type` blocks. Map trait -> implementors.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        traits: Dict[str, Any] = {}
        implementations: List[Dict[str, Any]] = []

        # Trait definitions
        trait_def = re.compile(
            r"(?:pub\s+)?trait\s+(\w+)\s*(?:<[^>]+>)?\s*"
            r"(?::\s*([\w\s+<>]+))?\s*\{",
            re.MULTILINE,
        )

        # Trait implementations
        impl_for = re.compile(
            r"impl\s+(?:<[^>]+>\s*)?(\w+)\s*(?:<[^>]+>)?\s+for\s+(\w+)\s*(?:<[^>]+>)?\s*\{",
            re.MULTILINE,
        )

        # Find trait definitions
        for finfo in all_files:
            content = finfo["content"]
            for td in trait_def.finditer(content):
                t_name = td.group(1)
                supertraits = td.group(2)

                if trait_name and t_name.lower() != trait_name.lower():
                    continue

                brace_start = content.find("{", td.start())
                block = self._extract_brace_block(content, brace_start)

                # Extract required methods
                required_methods = []
                for mm in re.finditer(
                    r"fn\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([\w<>:&'\s\[\](),!]+?))?\s*;",
                    block,
                ):
                    required_methods.append({
                        "name": mm.group(1),
                        "params": mm.group(2).strip()[:80],
                        "return_type": mm.group(3).strip() if mm.group(3) else "()",
                    })

                traits[t_name] = {
                    "file": finfo["rel_path"],
                    "supertraits": supertraits.strip() if supertraits else None,
                    "required_methods": required_methods,
                    "implementors": [],
                }

        # Find implementations
        for finfo in all_files:
            content = finfo["content"]
            for ifm in impl_for.finditer(content):
                t_name = ifm.group(1)
                implementor = ifm.group(2)

                if trait_name and t_name.lower() != trait_name.lower():
                    continue

                impl_info = {
                    "trait": t_name,
                    "implementor": implementor,
                    "file": finfo["rel_path"],
                }
                implementations.append(impl_info)

                if t_name in traits:
                    traits[t_name]["implementors"].append({
                        "type": implementor,
                        "file": finfo["rel_path"],
                    })

        return {
            "status": "ok",
            "traits": traits,
            "implementations": implementations,
            "total_traits": len(traits),
            "total_implementations": len(implementations),
        }

    # ── 7. get_error_handling ──────────────────────────────────────────

    def get_error_handling(self, handler_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Result<T, E> return types, ? operator chains, custom error types,
        error response mappers.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        error_types: Dict[str, Any] = {}
        handler_errors: List[Dict[str, Any]] = []

        # Custom error enum with thiserror
        thiserror_enum = re.compile(
            r"#\[derive\([^)]*(?:Error|thiserror)[^)]*\)\]\s*"
            r"pub\s+enum\s+(\w+)\s*\{([^}]+)\}",
            re.DOTALL,
        )

        # Error variant with #[error("...")]
        error_variant = re.compile(
            r'#\[error\(\s*"([^"]+)"\s*\)\]\s*(\w+)',
        )

        # impl ResponseError (Actix)
        response_error = re.compile(
            r"impl\s+ResponseError\s+for\s+(\w+)",
        )

        # impl IntoResponse (Axum)
        into_response = re.compile(
            r"impl\s+IntoResponse\s+for\s+(\w+)",
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Parse thiserror enums
            for te in thiserror_enum.finditer(content):
                enum_name = te.group(1)
                variants_block = te.group(2)

                variants = []
                for ev in error_variant.finditer(variants_block):
                    variants.append({
                        "name": ev.group(2),
                        "message": ev.group(1),
                    })

                error_types[enum_name] = {
                    "file": rel_path,
                    "variants": variants,
                    "has_response_error": bool(response_error.search(content)),
                    "has_into_response": bool(into_response.search(content)),
                }

            # Parse handler error patterns
            if handler_name:
                fn_pattern = re.compile(
                    rf"(?:pub\s+)?(?:async\s+)?fn\s+{re.escape(handler_name)}\s*"
                    rf"\(([^)]*)\)\s*->\s*([\w<>:&'\s\[\](),!]+?)\s*\{{",
                )
                fm = fn_pattern.search(content)
                if fm:
                    return_type = fm.group(2).strip()
                    brace_start = content.find("{", fm.start())
                    body = self._extract_brace_block(content, brace_start)

                    # Count ? operator usage
                    q_count = body.count("?")
                    map_err_count = len(re.findall(r"\.map_err\s*\(", body))
                    unwrap_count = len(re.findall(r"\.unwrap\s*\(\s*\)", body))

                    handler_errors.append({
                        "handler": handler_name,
                        "file": rel_path,
                        "return_type": return_type,
                        "question_mark_count": q_count,
                        "map_err_count": map_err_count,
                        "unwrap_count": unwrap_count,
                        "uses_anyhow": "anyhow" in body,
                    })

        return {
            "status": "ok",
            "error_types": error_types,
            "handler_errors": handler_errors,
            "total_error_types": len(error_types),
        }

    # ── 8. verify_endpoint ─────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check route registered, handler signature, extractors match.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        checks: List[Dict[str, Any]] = []
        overall_status = "pass"

        route_map = self.get_route_map()
        if route_map.get("status") != "ok":
            return {"status": "error", "message": "Could not parse routes"}

        matched = None
        for rt in route_map.get("routes", []):
            if rt["http_method"] == method.upper():
                rt_route = re.sub(r"\{[^}]+\}", "{id}", rt["route"])
                check_url = re.sub(r"\{[^}]+\}", "{id}", url)
                if rt_route == check_url or rt["route"] == url:
                    matched = rt
                    break

        if matched:
            checks.append({
                "check": "route_exists",
                "status": "pass",
                "detail": f"Route {method} {url} -> {matched['handler']}",
            })

            if matched.get("extractors"):
                checks.append({
                    "check": "extractors",
                    "status": "pass",
                    "detail": f"Extractors: {matched['extractors']}",
                })

            if matched.get("guard"):
                checks.append({
                    "check": "guard",
                    "status": "pass",
                    "detail": f"Guard: {matched['guard']}",
                })
        else:
            checks.append({
                "check": "route_exists",
                "status": "fail",
                "detail": f"No route found for {method} {url}",
            })
            overall_status = "fail"

        return {
            "status": "ok",
            "method": method,
            "url": url,
            "overall": overall_status,
            "checks": checks,
            "matched_route": matched,
            "framework": route_map.get("framework_detected"),
        }

    # ── 9. get_project_conventions ─────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Patterns: naming, error-handling, async, testing, module-structure.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}

        if pattern_type == "naming":
            struct_names = []
            fn_names = []
            mod_names = []
            for finfo in all_files:
                content = finfo["content"]
                for sm in re.finditer(r"pub\s+struct\s+(\w+)", content):
                    struct_names.append(sm.group(1))
                for fm in re.finditer(r"pub\s+(?:async\s+)?fn\s+(\w+)", content):
                    fn_names.append(fm.group(1))
                for mm in re.finditer(r"pub\s+mod\s+(\w+)", content):
                    mod_names.append(mm.group(1))

            snake_fns = sum(1 for n in fn_names if "_" in n or n.islower())
            pascal_structs = sum(1 for n in struct_names if n[0:1].isupper())

            conventions["findings"] = [{
                "convention": "naming_compliance",
                "snake_case_functions": f"{snake_fns}/{len(fn_names)}",
                "pascal_case_structs": f"{pascal_structs}/{len(struct_names)}",
                "modules": len(mod_names),
            }]

        elif pattern_type == "error-handling":
            uses_thiserror = 0
            uses_anyhow = 0
            uses_unwrap = 0
            uses_question_mark = 0

            for finfo in all_files:
                content = finfo["content"]
                if "thiserror" in content:
                    uses_thiserror += 1
                if "anyhow" in content:
                    uses_anyhow += 1
                uses_unwrap += len(re.findall(r"\.unwrap\s*\(\s*\)", content))
                uses_question_mark += content.count("?")

            conventions["findings"] = [{
                "convention": "error_handling",
                "thiserror_files": uses_thiserror,
                "anyhow_files": uses_anyhow,
                "unwrap_count": uses_unwrap,
                "question_mark_count": uses_question_mark,
            }]

        elif pattern_type == "async":
            async_fns = 0
            tokio_spawns = 0
            await_count = 0

            for finfo in all_files:
                content = finfo["content"]
                async_fns += len(re.findall(r"async\s+fn\s+", content))
                tokio_spawns += len(re.findall(r"tokio::spawn\s*\(", content))
                await_count += len(re.findall(r"\.await\b", content))

            conventions["findings"] = [{
                "convention": "async_patterns",
                "async_functions": async_fns,
                "tokio_spawns": tokio_spawns,
                "await_expressions": await_count,
            }]

        elif pattern_type == "testing":
            test_fns = 0
            test_modules = 0
            integration_tests = 0

            for finfo in all_files:
                content = finfo["content"]
                test_fns += len(re.findall(r"#\[test\]|#\[tokio::test\]", content))
                test_modules += len(re.findall(r"#\[cfg\(test\)\]", content))
                if "tests/" in finfo["rel_path"]:
                    integration_tests += 1

            conventions["findings"] = [{
                "convention": "testing",
                "test_functions": test_fns,
                "test_modules": test_modules,
                "integration_test_files": integration_tests,
            }]

        elif pattern_type == "module-structure":
            modules = {}
            for finfo in all_files:
                parts = finfo["rel_path"].split(os.sep)
                if len(parts) >= 2 and parts[0] == "src":
                    mod = parts[1] if len(parts) > 2 else os.path.splitext(parts[1])[0]
                    modules[mod] = modules.get(mod, 0) + 1

            conventions["findings"] = [{
                "convention": "module_structure",
                "modules": modules,
                "total_files": len(all_files),
            }]

        return {"status": "ok", **conventions}

    # ── 10. get_project_snapshot ───────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview: crates, modules, routes, structs, traits, impl blocks, tests.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)

        struct_names: List[str] = []
        trait_names: List[str] = []
        fn_names: List[str] = []
        test_count = 0
        mod_count = 0

        for finfo in all_files:
            content = finfo["content"]
            for sm in re.finditer(r"pub\s+struct\s+(\w+)", content):
                struct_names.append(sm.group(1))
            for tm in re.finditer(r"pub\s+trait\s+(\w+)", content):
                trait_names.append(tm.group(1))
            for fm in re.finditer(r"pub\s+(?:async\s+)?fn\s+(\w+)", content):
                fn_names.append(fm.group(1))
            test_count += len(re.findall(r"#\[test\]|#\[tokio::test\]", content))
            mod_count += len(re.findall(r"pub\s+mod\s+(\w+)", content))

        # Parse Cargo.toml
        cargo_info = {}
        cargo_path = os.path.join(base, "Cargo.toml")
        if os.path.isfile(cargo_path):
            try:
                with open(cargo_path, "r") as f:
                    cargo_content = f.read()
                name_m = re.search(r'name\s*=\s*"([^"]+)"', cargo_content)
                version_m = re.search(r'version\s*=\s*"([^"]+)"', cargo_content)
                edition_m = re.search(r'edition\s*=\s*"([^"]+)"', cargo_content)
                cargo_info = {
                    "name": name_m.group(1) if name_m else None,
                    "version": version_m.group(1) if version_m else None,
                    "edition": edition_m.group(1) if edition_m else None,
                }

                # Detect web framework
                if "actix-web" in cargo_content:
                    cargo_info["web_framework"] = "actix-web"
                elif "axum" in cargo_content:
                    cargo_info["web_framework"] = "axum"
                elif "rocket" in cargo_content:
                    cargo_info["web_framework"] = "rocket"

                # Dependencies
                deps = []
                in_deps = False
                for line in cargo_content.split("\n"):
                    if line.strip() == "[dependencies]":
                        in_deps = True
                        continue
                    if line.strip().startswith("[") and in_deps:
                        break
                    if in_deps:
                        dm = re.match(r'(\w[\w-]*)\s*=', line)
                        if dm:
                            deps.append(dm.group(1))
                cargo_info["dependencies"] = deps[:30]
            except Exception:
                pass

        # Workspace members
        workspace_members = []
        for wm in re.finditer(r'members\s*=\s*\[([^\]]+)\]', cargo_content if os.path.isfile(cargo_path) else ""):
            workspace_members = [m.strip().strip('"').strip("'") for m in wm.group(1).split(",")]

        route_map = self.get_route_map()

        return {
            "status": "ok",
            "cargo": cargo_info,
            "workspace_members": workspace_members,
            "structs": sorted(struct_names)[:50],
            "traits": sorted(trait_names)[:30],
            "public_functions": len(fn_names),
            "modules": mod_count,
            "routes": route_map.get("total", 0),
            "framework": route_map.get("framework_detected", "unknown"),
            "test_count": test_count,
            "total_rs_files": len(all_files),
            "summary": {
                "structs": len(struct_names),
                "traits": len(trait_names),
                "functions": len(fn_names),
                "tests": test_count,
            },
        }

    # ── 11. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Registry: middleware, extractor, error-handler, auth-guard,
        rate-limiter, websocket, background-task.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        desc_lower = description.lower()

        matched_types = []
        for ptype, pinfo in self.PATTERN_REGISTRY.items():
            if any(word in desc_lower for word in ptype.split("-")):
                matched_types.append(ptype)

        if not matched_types:
            matched_types = list(self.PATTERN_REGISTRY.keys())

        results: List[Dict[str, Any]] = []
        for ptype in matched_types:
            pinfo = self.PATTERN_REGISTRY[ptype]
            matches = []
            for finfo in all_files:
                content = finfo["content"]
                for pat_str in pinfo["patterns"]:
                    for pm in re.finditer(pat_str, content):
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

        keyword_matches = []
        keywords = desc_lower.split()
        for finfo in all_files:
            content = finfo["content"]
            for kw in keywords:
                if len(kw) < 3:
                    continue
                for cm in re.finditer(
                    rf"(?:pub\s+)?(?:struct|trait|fn|enum)\s+(\w*{re.escape(kw)}\w*)",
                    content, re.IGNORECASE,
                ):
                    keyword_matches.append({
                        "file": finfo["rel_path"],
                        "symbol": cm.group(1),
                    })
                    if len(keyword_matches) >= 15:
                        break

        return {
            "status": "ok",
            "query": description,
            "pattern_matches": results,
            "keyword_matches": keyword_matches[:15],
        }

    # ── 12. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware: struct->check impls/users, trait->check implementors,
        handler->check route/middleware.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_rs_files(base)
        result: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name,
            "references": [],
            "impact": [],
            "warnings": [],
        }

        for finfo in all_files:
            content = finfo["content"]
            if symbol_name not in content:
                continue
            lines = content.split("\n")
            refs = []
            for i, line in enumerate(lines, 1):
                if symbol_name in line:
                    refs.append({"line": i, "text": line.strip()[:120]})
            if refs:
                result["references"].append({
                    "file": finfo["rel_path"],
                    "usages": refs[:10],
                    "count": len(refs),
                })

        # Context-aware checks
        if "handler" in file_path or "routes" in file_path:
            result["impact"].append({"type": "route_check", "detail": "Check route registration for handler"})
            result["impact"].append({"type": "middleware_check", "detail": "Check middleware applied to route"})
        elif "model" in file_path or "schema" in file_path:
            result["impact"].append({"type": "migration_check", "detail": "Schema change may need migration"})
            result["impact"].append({"type": "query_check", "detail": "Check queries using this struct"})
        elif "service" in file_path:
            result["impact"].append({"type": "handler_check", "detail": "Check handlers using this service"})
        elif "error" in file_path:
            result["impact"].append({"type": "error_check", "detail": "Check all ? propagation sites"})

        # Check for impl blocks referencing symbol
        for finfo in all_files:
            content = finfo["content"]
            if re.search(rf"impl\s+(?:\w+\s+for\s+)?{re.escape(symbol_name)}", content):
                result["impact"].append({
                    "type": "impl_block",
                    "detail": f"impl block for {symbol_name} in {finfo['rel_path']}",
                })

        # Anti-patterns
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
