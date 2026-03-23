"""
Go Intelligence Service - Go-specific code analysis for Gin/Echo/Chi frameworks.

Provides struct relationship mapping, route parsing for multiple Go web frameworks,
migration analysis, middleware chain resolution, interface implementation tracking,
and package dependency graphing.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class GoIntelligenceService(BaseService):
    """Go-specific code intelligence for Gin/Echo/Chi/net-http projects."""

    SCAN_DIRS = {
        "models": "internal/models",
        "controllers": "internal/handlers",
        "services": "internal/services",
        "middleware": "internal/middleware",
        "routes": "internal/routes",
        "tests": ".",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bfmt\.Println\s*\(",
            "severity": "warning",
            "message": "fmt.Println in production code -- use log package or structured logger",
            "file_types": ["go"],
        },
        {
            "pattern": r"\bpanic\s*\(",
            "severity": "error",
            "message": "panic() in library code -- return errors instead",
            "file_types": ["go"],
        },
        {
            "pattern": r"_\s*=\s*\w+\.\w+\(",
            "severity": "warning",
            "message": "Ignoring error with _ = -- handle or explicitly document why",
            "file_types": ["go"],
        },
    ]

    PATTERN_REGISTRY = {
        "middleware": {
            "patterns": [
                r"\.Use\s*\(",
                r"func\s+\w+Middleware\s*\(",
                r"func\s+\(\w+\s+\*?\w+\)\s+\w+Middleware\s*\(",
            ],
            "description": "HTTP middleware patterns",
        },
        "handler": {
            "patterns": [
                r"func\s+\w+Handler\s*\(",
                r"func\s+\(\w+\s+\*?\w+\)\s+\w+\s*\(\s*c\s+\*?(?:gin\.Context|echo\.Context)",
                r"func\s+\w+\s*\(\s*w\s+http\.ResponseWriter",
            ],
            "description": "HTTP handler patterns",
        },
        "repository": {
            "patterns": [
                r"type\s+\w+Repository\s+(?:struct|interface)",
                r"func\s+\(\w+\s+\*?\w+Repository\)",
                r"\.Find\s*\(|\.Create\s*\(|\.Update\s*\(|\.Delete\s*\(",
            ],
            "description": "Repository pattern implementations",
        },
        "service": {
            "patterns": [
                r"type\s+\w+Service\s+(?:struct|interface)",
                r"func\s+\(\w+\s+\*?\w+Service\)",
                r"func\s+New\w+Service\s*\(",
            ],
            "description": "Service layer patterns",
        },
        "worker": {
            "patterns": [
                r"go\s+func\s*\(",
                r"go\s+\w+\.\w+\(",
                r"<-\s*\w+",
                r"\w+\s*<-",
                r"sync\.WaitGroup",
                r"sync\.Mutex",
            ],
            "description": "Goroutine and worker patterns",
        },
        "config": {
            "patterns": [
                r"type\s+Config\s+struct",
                r"viper\.",
                r"os\.Getenv\s*\(",
                r"godotenv",
                r"envconfig",
            ],
            "description": "Configuration patterns",
        },
        "logger": {
            "patterns": [
                r"log\.\w+\s*\(",
                r"zap\.",
                r"logrus\.",
                r"zerolog\.",
                r"slog\.",
            ],
            "description": "Logging patterns",
        },
        "validator": {
            "patterns": [
                r"validate\.\w+",
                r"binding:\"",
                r"validator\.",
                r"valid\.\w+",
            ],
            "description": "Validation patterns",
        },
    }

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
        return None

    def _scan_files(self, base: str, directory: str, extensions: tuple = (".go",)) -> List[dict]:
        """Scan directory recursively for Go files.

        Returns list of dicts with 'path', 'rel_path', and 'content'.
        """
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            # Skip vendor and hidden directories
            dirs[:] = [d for d in dirs if d not in ("vendor", ".git", "node_modules")]
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

    def _scan_all_go_files(self, base: str) -> List[dict]:
        """Scan all Go files in the project, excluding vendor."""
        all_files = []
        seen = set()

        # Scan known directories first
        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        # Also scan common Go directories
        extra_dirs = ["cmd", "pkg", "api", "internal", "app", "."]
        for d in extra_dirs:
            for f in self._scan_files(base, d):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

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

    # ── 1. get_struct_relationships ───────────────────────────────────

    def get_struct_relationships(self, struct_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse GORM struct tags and plain struct fields to extract relationships.

        Detects: gorm:"foreignKey:...", gorm:"many2many:...", belongs_to, has_many,
        pointer/slice types indicating relations.

        Args:
            struct_name: Optional struct name to filter. If omitted, returns all.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)
        all_structs: Dict[str, Any] = {}

        # Pattern to find struct definitions
        struct_pattern = re.compile(
            r"type\s+(\w+)\s+struct\s*\{",
            re.MULTILINE,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Skip test files for model analysis
            if "_test.go" in rel_path:
                continue

            for match in struct_pattern.finditer(content):
                name = match.group(1)
                if struct_name and name.lower() != struct_name.lower():
                    continue

                block_start = content.find("{", match.start())
                block = self._extract_brace_block(content, block_start)

                fields = []
                relations = []
                gorm_tags: List[Dict[str, Any]] = []

                # Parse each field line in the struct
                for line in block.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//"):
                        continue

                    # Parse field: FieldName Type `tag:"value"`
                    field_match = re.match(
                        r"(\w+)\s+([\[\]*\w.]+)\s*(?:`([^`]*)`)?",
                        line,
                    )
                    if not field_match:
                        # Embedded struct: just a type name
                        embed_match = re.match(r"(\*?\w+)\s*(?:`([^`]*)`)?$", line)
                        if embed_match:
                            fields.append({
                                "name": embed_match.group(1),
                                "type": embed_match.group(1),
                                "embedded": True,
                            })
                        continue

                    field_name = field_match.group(1)
                    field_type = field_match.group(2)
                    tag_str = field_match.group(3) or ""

                    field_info: Dict[str, Any] = {
                        "name": field_name,
                        "type": field_type,
                    }

                    # Parse GORM tags
                    gorm_tag = re.search(r'gorm:"([^"]*)"', tag_str)
                    if gorm_tag:
                        tag_value = gorm_tag.group(1)
                        field_info["gorm_tag"] = tag_value

                        # Extract GORM relationship info
                        fk_match = re.search(r"foreignKey:(\w+)", tag_value)
                        m2m_match = re.search(r"many2many:(\w+)", tag_value)
                        ref_match = re.search(r"references:(\w+)", tag_value)

                        if fk_match:
                            relations.append({
                                "field": field_name,
                                "type": "foreign_key",
                                "related_type": field_type.lstrip("*[]"),
                                "foreign_key": fk_match.group(1),
                            })
                        if m2m_match:
                            relations.append({
                                "field": field_name,
                                "type": "many2many",
                                "related_type": field_type.lstrip("*[]"),
                                "join_table": m2m_match.group(1),
                            })
                        if ref_match and not fk_match and not m2m_match:
                            relations.append({
                                "field": field_name,
                                "type": "reference",
                                "related_type": field_type.lstrip("*[]"),
                                "references": ref_match.group(1),
                            })

                        gorm_tags.append({"field": field_name, "tag": tag_value})

                    # Parse JSON tags
                    json_tag = re.search(r'json:"([^"]*)"', tag_str)
                    if json_tag:
                        field_info["json_name"] = json_tag.group(1).split(",")[0]

                    # Parse validate tags
                    validate_tag = re.search(r'validate:"([^"]*)"', tag_str)
                    if validate_tag:
                        field_info["validation"] = validate_tag.group(1)

                    # Detect implicit relations from type
                    clean_type = field_type.lstrip("*[]")
                    if (
                        clean_type[0:1].isupper()
                        and clean_type not in ("string", "int", "int64", "float64", "bool", "time.Time",
                                               "uint", "uint64", "int32", "float32", "byte", "rune")
                        and "." not in clean_type
                    ):
                        is_slice = field_type.startswith("[]")
                        is_pointer = field_type.startswith("*")
                        rel_type = "has_many" if is_slice else ("belongs_to" if is_pointer else "has_one")

                        # Don't duplicate if already captured via GORM tag
                        if not any(r["field"] == field_name for r in relations):
                            relations.append({
                                "field": field_name,
                                "type": rel_type,
                                "related_type": clean_type,
                                "inferred": True,
                            })

                    fields.append(field_info)

                all_structs[name] = {
                    "file": rel_path,
                    "fields": fields,
                    "relations": relations,
                    "gorm_tags": gorm_tags,
                }

        if struct_name and not all_structs:
            return {"status": "error", "message": f"Struct '{struct_name}' not found"}

        return {
            "status": "success",
            "structs": all_structs,
            "total_structs": len(all_structs),
        }

    # ── 2. get_route_map ──────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse route definitions for Gin, Echo, Chi, and net/http.

        Args:
            filter_prefix: Optional URL prefix filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)
        routes: List[Dict[str, Any]] = []

        # Gin patterns: r.GET("/path", handler), group.POST(...)
        gin_route_pattern = re.compile(
            r"(\w+)\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|Any)\s*\(\s*\"([^\"]+)\"\s*,\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # Echo patterns: e.GET("/path", handler)
        echo_route_pattern = re.compile(
            r"(\w+)\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(\s*\"([^\"]+)\"\s*,\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # Chi patterns: r.Get("/path", handler), r.Route("/prefix", ...)
        chi_route_pattern = re.compile(
            r"(\w+)\.(Get|Post|Put|Patch|Delete|Options|Head)\s*\(\s*\"([^\"]+)\"\s*,\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # net/http patterns: http.HandleFunc("/path", handler)
        http_pattern = re.compile(
            r"(?:http\.HandleFunc|http\.Handle|mux\.HandleFunc|mux\.Handle)\s*\(\s*\"([^\"]+)\"\s*,\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # Group patterns for Gin
        gin_group_pattern = re.compile(
            r"(\w+)\s*:?=\s*(\w+)\.Group\s*\(\s*\"([^\"]+)\"",
            re.MULTILINE,
        )

        # Chi Route group
        chi_route_pattern_group = re.compile(
            r"(\w+)\.Route\s*\(\s*\"([^\"]+)\"\s*,\s*func\s*\(\s*(\w+)\s+chi\.Router\s*\)",
            re.MULTILINE,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Build group prefix map
            group_prefixes: Dict[str, str] = {}

            for gm in gin_group_pattern.finditer(content):
                group_var = gm.group(1)
                parent_var = gm.group(2)
                prefix = gm.group(3)
                parent_prefix = group_prefixes.get(parent_var, "")
                group_prefixes[group_var] = parent_prefix + prefix

            for gm in chi_route_pattern_group.finditer(content):
                parent_var = gm.group(1)
                prefix = gm.group(2)
                router_var = gm.group(3)
                parent_prefix = group_prefixes.get(parent_var, "")
                group_prefixes[router_var] = parent_prefix + prefix

            # Parse Gin/Echo routes
            for match in gin_route_pattern.finditer(content):
                router_var = match.group(1)
                method = match.group(2).upper()
                path = match.group(3)
                handler = match.group(4).strip().rstrip(",)")

                prefix = group_prefixes.get(router_var, "")
                full_path = prefix + path

                routes.append({
                    "method": method,
                    "path": full_path,
                    "handler": handler,
                    "framework": self._detect_go_framework(content),
                    "file": rel_path,
                })

            # Parse Chi routes
            for match in chi_route_pattern.finditer(content):
                router_var = match.group(1)
                method = match.group(2).upper()
                path = match.group(3)
                handler = match.group(4).strip().rstrip(",)")

                prefix = group_prefixes.get(router_var, "")
                full_path = prefix + path

                # Avoid duplicates from gin_route_pattern
                if not any(r["path"] == full_path and r["method"] == method for r in routes):
                    routes.append({
                        "method": method,
                        "path": full_path,
                        "handler": handler,
                        "framework": "chi",
                        "file": rel_path,
                    })

            # Parse net/http routes
            for match in http_pattern.finditer(content):
                path = match.group(1)
                handler = match.group(2).strip().rstrip(",)")

                routes.append({
                    "method": "ANY",
                    "path": path,
                    "handler": handler,
                    "framework": "net/http",
                    "file": rel_path,
                })

        # Deduplicate
        seen: Set[Tuple[str, str]] = set()
        unique_routes = []
        for r in routes:
            key = (r["method"], r["path"])
            if key not in seen:
                seen.add(key)
                unique_routes.append(r)
        routes = unique_routes

        # Apply filter
        if filter_prefix:
            routes = [r for r in routes if r["path"].startswith(filter_prefix)]

        total = len(routes)
        if not filter_prefix and total > 60:
            prefixes = sorted(set(
                "/" + r["path"].strip("/").split("/")[0]
                for r in routes if r["path"] and r["path"] != "/"
            ))
            return {
                "status": "success",
                "routes": routes[:60],
                "total": total,
                "truncated": True,
                "warning": f"Showing 60/{total} routes. Use filter_prefix to narrow.",
                "available_prefixes": prefixes[:20],
            }

        return {
            "status": "success",
            "routes": routes,
            "total": total,
            "filter": filter_prefix,
        }

    def _detect_go_framework(self, content: str) -> str:
        """Detect which Go web framework is used based on imports."""
        if "github.com/gin-gonic/gin" in content or "gin.Context" in content:
            return "gin"
        if "github.com/labstack/echo" in content or "echo.Context" in content:
            return "echo"
        if "github.com/go-chi/chi" in content or "chi.Router" in content:
            return "chi"
        if "net/http" in content:
            return "net/http"
        return "unknown"

    # ── 3. get_migration_schema ───────────────────────────────────────

    def get_migration_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse GORM AutoMigrate calls and golang-migrate .sql files to extract
        table/column information.

        Args:
            table_name: Optional table name filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        tables: Dict[str, Any] = {}

        # 1. Parse GORM AutoMigrate(&Model{}) calls
        all_go_files = self._scan_all_go_files(base)
        auto_migrate_models: List[str] = []

        for finfo in all_go_files:
            content = finfo["content"]
            # db.AutoMigrate(&User{}, &Post{})
            migrate_matches = re.findall(
                r"AutoMigrate\s*\(([^)]+)\)",
                content,
            )
            for match_str in migrate_matches:
                models = re.findall(r"&(\w+)\{\}", match_str)
                auto_migrate_models.extend(models)

        # Get struct info for AutoMigrate models
        struct_info = self.get_struct_relationships()
        if struct_info.get("status") == "success":
            for model_name in auto_migrate_models:
                struct_data = struct_info.get("structs", {}).get(model_name)
                if not struct_data:
                    continue

                # Convert struct name to table name (snake_case + plural)
                tname = self._go_struct_to_table_name(model_name)
                if table_name and tname.lower() != table_name.lower():
                    continue

                columns = []
                indexes = []
                foreign_keys = []

                for field in struct_data.get("fields", []):
                    if field.get("embedded"):
                        continue
                    col_name = field.get("json_name") or self._camel_to_snake(field["name"])
                    col_type = self._go_type_to_sql(field["type"])

                    col_info: Dict[str, Any] = {
                        "name": col_name,
                        "type": col_type,
                        "go_type": field["type"],
                    }

                    gorm_tag = field.get("gorm_tag", "")
                    if "primaryKey" in gorm_tag:
                        col_info["primary_key"] = True
                    if "not null" in gorm_tag:
                        col_info["nullable"] = False
                    if "unique" in gorm_tag:
                        col_info["unique"] = True
                    if "index" in gorm_tag:
                        indexes.append({"column": col_name})
                    default_match = re.search(r"default:(\w+)", gorm_tag)
                    if default_match:
                        col_info["default"] = default_match.group(1)

                    columns.append(col_info)

                for rel in struct_data.get("relations", []):
                    if rel["type"] in ("foreign_key", "belongs_to"):
                        foreign_keys.append({
                            "column": rel.get("foreign_key", rel["field"] + "ID"),
                            "references": rel["related_type"],
                        })

                tables[tname] = {
                    "model": model_name,
                    "columns": columns,
                    "indexes": indexes,
                    "foreign_keys": foreign_keys,
                    "source": "gorm_automigrate",
                }

        # 2. Parse golang-migrate .sql files
        migration_dirs = [
            "migrations",
            "db/migrations",
            "database/migrations",
            "internal/database/migrations",
        ]

        for mdir in migration_dirs:
            full_dir = os.path.join(base, mdir)
            if not os.path.isdir(full_dir):
                continue

            for fname in sorted(os.listdir(full_dir)):
                if not fname.endswith(".up.sql"):
                    continue
                fpath = os.path.join(full_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        sql_content = f.read()
                except Exception:
                    continue

                rel_path = os.path.relpath(fpath, base)

                # CREATE TABLE
                create_pattern = re.compile(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\(",
                    re.IGNORECASE,
                )
                for ct_match in create_pattern.finditer(sql_content):
                    tname = ct_match.group(1)
                    if table_name and tname.lower() != table_name.lower():
                        continue

                    # Extract column definitions
                    paren_start = sql_content.find("(", ct_match.end() - 1)
                    if paren_start == -1:
                        continue

                    # Find matching closing paren
                    depth = 0
                    pos = paren_start
                    while pos < len(sql_content):
                        if sql_content[pos] == "(":
                            depth += 1
                        elif sql_content[pos] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        pos += 1

                    table_block = sql_content[paren_start + 1:pos]
                    columns = self._parse_sql_columns(table_block)

                    if tname not in tables:
                        tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "source": "sql_migration"}
                    tables[tname]["columns"].extend(columns)
                    tables[tname]["file"] = rel_path

                # ALTER TABLE ADD COLUMN
                alter_pattern = re.compile(
                    r"ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?\s+ADD\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?\s+(\w+)",
                    re.IGNORECASE,
                )
                for alt_match in alter_pattern.finditer(sql_content):
                    tname = alt_match.group(1)
                    if table_name and tname.lower() != table_name.lower():
                        continue
                    if tname not in tables:
                        tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "source": "sql_migration"}
                    tables[tname]["columns"].append({
                        "name": alt_match.group(2),
                        "type": alt_match.group(3),
                        "operation": "add_column",
                        "file": rel_path,
                    })

                # CREATE INDEX
                idx_pattern = re.compile(
                    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON\s+[`\"]?(\w+)[`\"]?\s*\(([^)]+)\)",
                    re.IGNORECASE,
                )
                for idx_match in idx_pattern.finditer(sql_content):
                    tname = idx_match.group(1)
                    if table_name and tname.lower() != table_name.lower():
                        continue
                    if tname not in tables:
                        tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "source": "sql_migration"}
                    idx_cols = [c.strip().strip("`\"") for c in idx_match.group(2).split(",")]
                    tables[tname]["indexes"].append({"columns": idx_cols, "file": rel_path})

        if table_name and not tables:
            return {"status": "error", "message": f"Table '{table_name}' not found"}

        return {
            "status": "success",
            "tables": tables,
            "total_tables": len(tables),
        }

    def _parse_sql_columns(self, block: str) -> List[Dict[str, Any]]:
        """Parse SQL column definitions from a CREATE TABLE block."""
        columns = []
        for line in block.split(","):
            line = line.strip()
            if not line:
                continue
            # Skip constraints
            upper = line.upper()
            if upper.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "KEY")):
                continue
            col_match = re.match(r"[`\"]?(\w+)[`\"]?\s+(\w+(?:\([^)]*\))?)", line)
            if col_match:
                col_info: Dict[str, Any] = {
                    "name": col_match.group(1),
                    "type": col_match.group(2),
                }
                if "NOT NULL" in upper:
                    col_info["nullable"] = False
                if "DEFAULT" in upper:
                    default_match = re.search(r"DEFAULT\s+(\S+)", line, re.IGNORECASE)
                    if default_match:
                        col_info["default"] = default_match.group(1).strip("'\"")
                if "PRIMARY KEY" in upper:
                    col_info["primary_key"] = True
                columns.append(col_info)
        return columns

    def _go_struct_to_table_name(self, name: str) -> str:
        """Convert Go struct name to GORM-style table name (snake_case plural)."""
        snake = self._camel_to_snake(name)
        if snake.endswith("s") or snake.endswith("x") or snake.endswith("ch") or snake.endswith("sh"):
            return snake + "es"
        elif snake.endswith("y"):
            return snake[:-1] + "ies"
        return snake + "s"

    def _camel_to_snake(self, name: str) -> str:
        """Convert CamelCase to snake_case."""
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    def _go_type_to_sql(self, go_type: str) -> str:
        """Map Go types to SQL types."""
        mapping = {
            "string": "VARCHAR(255)",
            "int": "INTEGER",
            "int64": "BIGINT",
            "int32": "INTEGER",
            "uint": "INTEGER UNSIGNED",
            "uint64": "BIGINT UNSIGNED",
            "float64": "DOUBLE",
            "float32": "FLOAT",
            "bool": "BOOLEAN",
            "time.Time": "TIMESTAMP",
            "[]byte": "BLOB",
        }
        clean = go_type.lstrip("*")
        return mapping.get(clean, "TEXT")

    # ── 4. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace a complete request flow: Route -> middleware -> handler ->
        service -> repository -> model.

        Args:
            entry_point: Handler function name or route path.
            method: Optional HTTP method.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)
        flows: List[Dict[str, Any]] = []

        # Find matching routes
        route_map = self.get_route_map()
        if route_map.get("status") != "success":
            return route_map

        matching_routes = []
        for route in route_map.get("routes", []):
            path_match = entry_point.lower() in route["path"].lower()
            handler_match = entry_point.lower() in route.get("handler", "").lower()
            method_match = not method or route["method"].upper() == method.upper()

            if (path_match or handler_match) and method_match:
                matching_routes.append(route)

        if not matching_routes:
            return {"status": "error", "message": f"No routes matching '{entry_point}'"}

        for route in matching_routes:
            flow: Dict[str, Any] = {
                "route": {
                    "method": route["method"],
                    "path": route["path"],
                    "file": route.get("file"),
                },
                "middleware": [],
                "handler": route.get("handler"),
                "service_calls": [],
                "repository_calls": [],
                "model_operations": [],
                "response_type": None,
            }

            # Find handler implementation
            handler_name = route.get("handler", "")
            for finfo in all_files:
                content = finfo["content"]

                # Look for handler function definition
                handler_match = re.search(
                    rf"func\s+(?:\(\w+\s+\*?\w+\)\s+)?{re.escape(handler_name)}\s*\(",
                    content,
                )
                if not handler_match:
                    continue

                # Find the function body
                brace_start = content.find("{", handler_match.end())
                if brace_start == -1:
                    continue
                body = self._extract_brace_block(content, brace_start)

                # Service calls
                service_calls = re.findall(
                    r"(?:\w+)\.(\w+Service)\.(\w+)\s*\(",
                    body,
                )
                flow["service_calls"] = [
                    {"service": s, "method": m} for s, m in service_calls
                ]

                # Repository calls
                repo_calls = re.findall(
                    r"(?:\w+)\.(\w+Repository)\.(\w+)\s*\(",
                    body,
                )
                flow["repository_calls"] = [
                    {"repository": r, "method": m} for r, m in repo_calls
                ]

                # GORM operations
                gorm_ops = re.findall(
                    r"\.(?:db|DB)\s*\.\s*(Find|First|Create|Save|Update|Delete|Where|Preload|Joins|Raw|Exec)\s*\(",
                    body,
                )
                flow["model_operations"] = list(set(gorm_ops))

                # Response type
                if "c.JSON(" in body or "JSON(" in body:
                    flow["response_type"] = "JSON"
                elif "c.HTML(" in body or "HTML(" in body:
                    flow["response_type"] = "HTML"
                elif "c.String(" in body or "String(" in body:
                    flow["response_type"] = "String"
                elif "c.Redirect(" in body or "Redirect(" in body:
                    flow["response_type"] = "Redirect"

                break

            flows.append(flow)

        return {
            "status": "success",
            "entry_point": entry_point,
            "method": method,
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 5. get_middleware_chain ────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse router.Use() and group middleware in Gin/Echo/Chi.

        Args:
            route_path: Optional route path to filter middleware for.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)
        middleware_entries: List[Dict[str, Any]] = []
        order = 0

        # Gin: r.Use(middleware), group.Use(middleware)
        use_pattern = re.compile(
            r"(\w+)\.Use\s*\(\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # Group associations
        group_pattern = re.compile(
            r"(\w+)\s*:?=\s*(\w+)\.Group\s*\(\s*\"([^\"]+)\"",
            re.MULTILINE,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Build group prefix map
            group_info: Dict[str, Dict[str, str]] = {}
            for gm in group_pattern.finditer(content):
                group_var = gm.group(1)
                parent_var = gm.group(2)
                prefix = gm.group(3)
                parent_prefix = group_info.get(parent_var, {}).get("prefix", "")
                group_info[group_var] = {"prefix": parent_prefix + prefix, "parent": parent_var}

            # Parse Use() calls
            for match in use_pattern.finditer(content):
                router_var = match.group(1)
                mw_str = match.group(2)

                # Determine scope
                scope = "global"
                path = "*"
                if router_var in group_info:
                    scope = "group"
                    path = group_info[router_var]["prefix"]

                # Parse multiple middleware in one Use() call
                mw_names = [m.strip() for m in self._split_args(mw_str) if m.strip()]

                for mw_name in mw_names:
                    order += 1
                    middleware_entries.append({
                        "order": order,
                        "name": mw_name,
                        "scope": scope,
                        "path": path,
                        "router_var": router_var,
                        "file": rel_path,
                    })

        # Filter by route_path
        if route_path:
            filtered = []
            for mw in middleware_entries:
                if mw["scope"] == "global":
                    filtered.append(mw)
                elif mw["scope"] == "group" and route_path.startswith(mw["path"]):
                    filtered.append(mw)
            middleware_entries = filtered

        return {
            "status": "success",
            "middleware": middleware_entries,
            "total": len(middleware_entries),
            "route_filter": route_path,
        }

    def _split_args(self, args_str: str) -> List[str]:
        """Split function arguments respecting parentheses."""
        result = []
        depth = 0
        current = ""
        for ch in args_str:
            if ch in "({[":
                depth += 1
                current += ch
            elif ch in ")}]":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0:
                result.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            result.append(current.strip())
        return result

    # ── 6. get_interface_implementations ──────────────────────────────

    def get_interface_implementations(self, interface_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Find all types implementing an interface by method set matching.
        Essential for Go's implicit interface satisfaction.

        Args:
            interface_name: Optional interface name. If omitted, returns all interfaces with their implementations.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)

        # 1. Parse all interface definitions
        interfaces: Dict[str, Dict[str, Any]] = {}
        interface_pattern = re.compile(
            r"type\s+(\w+)\s+interface\s*\{",
            re.MULTILINE,
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            for match in interface_pattern.finditer(content):
                iface_name = match.group(1)
                if interface_name and iface_name.lower() != interface_name.lower():
                    continue

                block_start = content.find("{", match.start())
                block = self._extract_brace_block(content, block_start)

                # Parse method signatures
                methods = []
                for line in block.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//"):
                        continue
                    # Embedded interface
                    embed_match = re.match(r"^(\w+)$", line)
                    if embed_match:
                        methods.append({"name": embed_match.group(1), "embedded": True})
                        continue
                    # Method signature: MethodName(params) returns
                    method_match = re.match(r"(\w+)\s*\(([^)]*)\)\s*(.*)", line)
                    if method_match:
                        methods.append({
                            "name": method_match.group(1),
                            "params": method_match.group(2).strip(),
                            "returns": method_match.group(3).strip(),
                        })

                interfaces[iface_name] = {
                    "file": rel_path,
                    "methods": methods,
                    "implementations": [],
                }

        # 2. Parse all type method receivers to find implementations
        # func (r *Repo) MethodName(params) returns { ... }
        receiver_pattern = re.compile(
            r"func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)\s*\(",
            re.MULTILINE,
        )

        # Build a map: type -> set of methods
        type_methods: Dict[str, Set[str]] = {}
        type_files: Dict[str, str] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            for match in receiver_pattern.finditer(content):
                type_name = match.group(1)
                method_name = match.group(2)
                if type_name not in type_methods:
                    type_methods[type_name] = set()
                    type_files[type_name] = rel_path
                type_methods[type_name].add(method_name)

        # 3. Match types to interfaces
        for iface_name, iface_data in interfaces.items():
            required_methods = {
                m["name"] for m in iface_data["methods"]
                if not m.get("embedded")
            }

            if not required_methods:
                continue

            for type_name, methods in type_methods.items():
                if required_methods.issubset(methods):
                    iface_data["implementations"].append({
                        "type": type_name,
                        "file": type_files.get(type_name, "unknown"),
                        "matching_methods": sorted(required_methods),
                        "extra_methods": sorted(methods - required_methods),
                    })

        # Also check for compile-time interface checks: var _ Interface = (*Type)(nil)
        compile_check_pattern = re.compile(
            r"var\s+_\s+(\w+)\s*=\s*\(\*(\w+)\)\(nil\)",
        )
        for finfo in all_files:
            for match in compile_check_pattern.finditer(finfo["content"]):
                iface = match.group(1)
                impl_type = match.group(2)
                if iface in interfaces:
                    # Mark as explicitly verified
                    for impl in interfaces[iface]["implementations"]:
                        if impl["type"] == impl_type:
                            impl["compile_verified"] = True

        if interface_name and not interfaces:
            return {"status": "error", "message": f"Interface '{interface_name}' not found"}

        return {
            "status": "success",
            "interfaces": interfaces,
            "total_interfaces": len(interfaces),
        }

    # ── 7. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify an endpoint: route registration, handler signature, middleware.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: URL path (e.g., "/api/users")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        verification: Dict[str, Any] = {
            "method": method.upper(),
            "url": url,
            "checks": [],
            "warnings": [],
            "errors": [],
        }

        # 1. Check route registration
        route_map = self.get_route_map()
        route_found = False
        matched_route = None
        if route_map.get("status") == "success":
            for route in route_map.get("routes", []):
                if route["method"].upper() == method.upper() and self._go_paths_match(route["path"], url):
                    route_found = True
                    matched_route = route
                    break

        if route_found:
            verification["checks"].append({
                "check": "route_registered",
                "status": "pass",
                "detail": f"Route {method.upper()} {url} found in {matched_route.get('file', 'unknown')}",
            })
        else:
            verification["errors"].append({
                "check": "route_registered",
                "status": "fail",
                "detail": f"Route {method.upper()} {url} not found",
            })
            return {"status": "success", "verification": verification}

        # 2. Check handler exists
        handler_name = matched_route.get("handler", "")
        if handler_name:
            all_files = self._scan_all_go_files(base)
            handler_found = False

            # Strip receiver prefix if present (e.g., h.CreateUser -> CreateUser)
            clean_handler = handler_name.split(".")[-1] if "." in handler_name else handler_name

            for finfo in all_files:
                func_pattern = re.compile(
                    rf"func\s+(?:\(\w+\s+\*?\w+\)\s+)?{re.escape(clean_handler)}\s*\(",
                )
                if func_pattern.search(finfo["content"]):
                    handler_found = True
                    verification["checks"].append({
                        "check": "handler_implementation",
                        "status": "pass",
                        "detail": f"Handler '{clean_handler}' found in {finfo['rel_path']}",
                    })

                    # Check error handling
                    brace_start = finfo["content"].find("{", func_pattern.search(finfo["content"]).end())
                    if brace_start != -1:
                        body = self._extract_brace_block(finfo["content"], brace_start)
                        if "if err != nil" in body:
                            verification["checks"].append({
                                "check": "error_handling",
                                "status": "pass",
                                "detail": "Handler checks errors with 'if err != nil'",
                            })
                        else:
                            verification["warnings"].append({
                                "check": "error_handling",
                                "status": "warning",
                                "detail": "Handler may not check errors (no 'if err != nil' found)",
                            })
                    break

            if not handler_found:
                verification["warnings"].append({
                    "check": "handler_implementation",
                    "status": "warning",
                    "detail": f"Could not find handler '{clean_handler}' definition",
                })

        # 3. Check middleware
        mw_chain = self.get_middleware_chain(url)
        if mw_chain.get("status") == "success":
            mw_list = mw_chain.get("middleware", [])
            verification["checks"].append({
                "check": "middleware_chain",
                "status": "pass",
                "detail": f"{len(mw_list)} middleware(s) apply",
                "middleware": [m["name"] for m in mw_list[:10]],
            })

        return {
            "status": "success",
            "verification": verification,
        }

    def _go_paths_match(self, pattern: str, url: str) -> bool:
        """Check if a Go route pattern matches a URL, handling :param and {param}."""
        regex_pattern = re.sub(r"[:{\w+}]+", r"[^/]+", pattern)
        regex_pattern = "^" + regex_pattern + "$"
        try:
            return bool(re.match(regex_pattern, url))
        except re.error:
            return pattern == url

    # ── 8. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the Go project codebase.

        Args:
            pattern_type: One of: "naming", "error-handling", "concurrency", "testing"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_types = ["naming", "error-handling", "concurrency", "testing"]
        if pattern_type not in valid_types:
            return {"status": "error", "message": f"Invalid pattern_type. Choose from: {valid_types}"}

        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}
        all_files = self._scan_all_go_files(base)

        if pattern_type == "naming":
            package_names: Dict[str, int] = {}
            exported_funcs = 0
            unexported_funcs = 0

            for finfo in all_files:
                content = finfo["content"]
                # Package name
                pkg_match = re.search(r"^package\s+(\w+)", content, re.MULTILINE)
                if pkg_match:
                    pkg = pkg_match.group(1)
                    package_names[pkg] = package_names.get(pkg, 0) + 1

                # Count exported vs unexported functions
                exported = re.findall(r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?([A-Z]\w+)\s*\(", content)
                unexported = re.findall(r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?([a-z]\w+)\s*\(", content)
                exported_funcs += len(exported)
                unexported_funcs += len(unexported)

            conventions["findings"].append({
                "category": "packages",
                "package_names": package_names,
            })
            conventions["findings"].append({
                "category": "visibility",
                "exported_functions": exported_funcs,
                "unexported_functions": unexported_funcs,
            })

        elif pattern_type == "error-handling":
            patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if "if err != nil" in content:
                    patterns["err_nil_check"] = patterns.get("err_nil_check", 0) + content.count("if err != nil")
                if "errors.New(" in content:
                    patterns["errors_new"] = patterns.get("errors_new", 0) + content.count("errors.New(")
                if "fmt.Errorf(" in content:
                    patterns["fmt_errorf"] = patterns.get("fmt_errorf", 0) + content.count("fmt.Errorf(")
                if "errors.Wrap(" in content or "errors.Wrapf(" in content:
                    patterns["error_wrapping"] = patterns.get("error_wrapping", 0) + 1
                if "errors.Is(" in content:
                    patterns["errors_is"] = patterns.get("errors_is", 0) + content.count("errors.Is(")
                if "errors.As(" in content:
                    patterns["errors_as"] = patterns.get("errors_as", 0) + content.count("errors.As(")
                if "_ = " in content:
                    patterns["ignored_errors"] = patterns.get("ignored_errors", 0) + content.count("_ = ")

            conventions["findings"].append({
                "category": "error_handling",
                "patterns": patterns,
            })

        elif pattern_type == "concurrency":
            patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if "go func(" in content:
                    patterns["goroutine_anonymous"] = patterns.get("goroutine_anonymous", 0) + content.count("go func(")
                go_calls = re.findall(r"\bgo\s+\w+", content)
                patterns["goroutine_named"] = patterns.get("goroutine_named", 0) + len(go_calls)
                if "sync.WaitGroup" in content:
                    patterns["wait_group"] = patterns.get("wait_group", 0) + 1
                if "sync.Mutex" in content or "sync.RWMutex" in content:
                    patterns["mutex"] = patterns.get("mutex", 0) + 1
                if "chan " in content or "make(chan" in content:
                    patterns["channels"] = patterns.get("channels", 0) + 1
                if "context.Background()" in content or "context.TODO()" in content:
                    patterns["context_usage"] = patterns.get("context_usage", 0) + 1
                if "select {" in content:
                    patterns["select_statement"] = patterns.get("select_statement", 0) + content.count("select {")

            conventions["findings"].append({
                "category": "concurrency",
                "patterns": patterns,
            })

        elif pattern_type == "testing":
            test_patterns: Dict[str, int] = {}
            test_files = [f for f in all_files if f["rel_path"].endswith("_test.go")]

            for finfo in test_files:
                content = finfo["content"]
                # Table-driven tests
                if "tests := []struct" in content or "testCases := []struct" in content:
                    test_patterns["table_driven"] = test_patterns.get("table_driven", 0) + 1
                if "t.Run(" in content:
                    test_patterns["subtests"] = test_patterns.get("subtests", 0) + content.count("t.Run(")
                if "testing.T" in content:
                    test_patterns["unit_tests"] = test_patterns.get("unit_tests", 0) + 1
                if "testify" in content:
                    test_patterns["testify"] = test_patterns.get("testify", 0) + 1
                if "httptest" in content:
                    test_patterns["httptest"] = test_patterns.get("httptest", 0) + 1
                if "mock" in content.lower():
                    test_patterns["mocking"] = test_patterns.get("mocking", 0) + 1
                if "Benchmark" in content:
                    test_patterns["benchmarks"] = test_patterns.get("benchmarks", 0) + 1

            conventions["findings"].append({
                "category": "testing",
                "patterns": test_patterns,
                "test_files_found": len(test_files),
            })

        return {
            "status": "success",
            "conventions": conventions,
        }

    # ── 9. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns in the project.

        Args:
            description: Natural language description (e.g., "middleware", "repository").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        description_lower = description.lower()
        matches: List[Dict[str, Any]] = []

        matched_categories = []
        for category, info in self.PATTERN_REGISTRY.items():
            keywords = category.replace("-", " ").split()
            if any(kw in description_lower for kw in keywords) or category in description_lower:
                matched_categories.append(category)

        if not matched_categories:
            for word in description_lower.split():
                if len(word) < 3:
                    continue
                for category in self.PATTERN_REGISTRY:
                    if word in category or category in word:
                        if category not in matched_categories:
                            matched_categories.append(category)

        all_files = self._scan_all_go_files(base)

        for category in matched_categories:
            info = self.PATTERN_REGISTRY[category]
            category_matches: List[Dict[str, Any]] = []

            for finfo in all_files:
                content = finfo["content"]
                file_matches = []
                for pattern in info["patterns"]:
                    for line_match in re.finditer(pattern, content):
                        line_num = content[:line_match.start()].count("\n") + 1
                        lines = content.split("\n")
                        line_text = lines[line_num - 1].strip()[:120] if line_num <= len(lines) else ""
                        file_matches.append({
                            "line": line_num,
                            "text": line_text,
                            "pattern": pattern,
                        })

                if file_matches:
                    category_matches.append({
                        "file": finfo["rel_path"],
                        "matches": file_matches[:5],
                        "total_matches": len(file_matches),
                    })

            if category_matches:
                matches.append({
                    "category": category,
                    "description": info["description"],
                    "files": category_matches[:10],
                    "total_files": len(category_matches),
                })

        return {
            "status": "success",
            "query": description,
            "matched_patterns": matches,
            "total_categories": len(matches),
        }

    # ── 10. get_project_snapshot ──────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a Go project overview: packages, routes, models/structs,
        interfaces, middleware, go.mod dependencies.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "structure": {},
            "packages": [],
            "routes_summary": None,
            "structs_summary": None,
            "interfaces_summary": None,
            "middleware_summary": None,
            "dependencies": {},
        }

        # Directory structure
        for category, directory in self.SCAN_DIRS.items():
            full_dir = os.path.join(base, directory)
            if os.path.isdir(full_dir):
                file_count = 0
                for root, dirs, files in os.walk(full_dir):
                    dirs[:] = [d for d in dirs if d not in ("vendor", ".git")]
                    file_count += len([f for f in files if f.endswith(".go")])
                snapshot["structure"][category] = {
                    "path": directory,
                    "file_count": file_count,
                }

        # Package names
        all_files = self._scan_all_go_files(base)
        packages = set()
        for finfo in all_files:
            pkg_match = re.search(r"^package\s+(\w+)", finfo["content"], re.MULTILINE)
            if pkg_match:
                packages.add(pkg_match.group(1))
        snapshot["packages"] = sorted(packages)

        # Routes summary
        route_map = self.get_route_map()
        if route_map.get("status") == "success":
            routes = route_map.get("routes", [])
            method_counts: Dict[str, int] = {}
            frameworks: Set[str] = set()
            for r in routes:
                m = r["method"]
                method_counts[m] = method_counts.get(m, 0) + 1
                frameworks.add(r.get("framework", "unknown"))
            snapshot["routes_summary"] = {
                "total": len(routes),
                "by_method": method_counts,
                "frameworks": sorted(frameworks),
            }

        # Structs summary
        structs = self.get_struct_relationships()
        if structs.get("status") == "success":
            snapshot["structs_summary"] = {
                "total": structs.get("total_structs", 0),
                "structs": list(structs.get("structs", {}).keys())[:50],
            }

        # Interfaces summary
        interfaces = self.get_interface_implementations()
        if interfaces.get("status") == "success":
            iface_summary = []
            for name, data in interfaces.get("interfaces", {}).items():
                iface_summary.append({
                    "name": name,
                    "methods": len(data.get("methods", [])),
                    "implementations": len(data.get("implementations", [])),
                })
            snapshot["interfaces_summary"] = {
                "total": len(iface_summary),
                "interfaces": iface_summary[:20],
            }

        # Middleware summary
        middleware = self.get_middleware_chain()
        if middleware.get("status") == "success":
            snapshot["middleware_summary"] = {
                "total": middleware.get("total", 0),
            }

        # go.mod dependencies
        gomod_path = os.path.join(base, "go.mod")
        if os.path.isfile(gomod_path):
            try:
                with open(gomod_path, "r", encoding="utf-8") as f:
                    gomod_content = f.read()

                module_match = re.search(r"module\s+(\S+)", gomod_content)
                if module_match:
                    snapshot["dependencies"]["module"] = module_match.group(1)

                go_version_match = re.search(r"go\s+([\d.]+)", gomod_content)
                if go_version_match:
                    snapshot["dependencies"]["go_version"] = go_version_match.group(1)

                requires = re.findall(r"^\s+(\S+)\s+(v[\S]+)", gomod_content, re.MULTILINE)
                snapshot["dependencies"]["require"] = [
                    {"module": mod, "version": ver} for mod, ver in requires
                ]
            except Exception:
                pass

        return {
            "status": "success",
            "snapshot": snapshot,
        }

    # ── 11. get_dependency_graph ──────────────────────────────────────

    def get_dependency_graph(self, package_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse import statements across all .go files to build a package dependency graph.

        Args:
            package_name: Optional package name to focus the graph on.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_go_files(base)

        # Get module name from go.mod
        module_name = ""
        gomod_path = os.path.join(base, "go.mod")
        if os.path.isfile(gomod_path):
            try:
                with open(gomod_path, "r", encoding="utf-8") as f:
                    gomod = f.read()
                mod_match = re.search(r"module\s+(\S+)", gomod)
                if mod_match:
                    module_name = mod_match.group(1)
            except Exception:
                pass

        # Build dependency graph
        graph: Dict[str, Dict[str, Any]] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Get package name
            pkg_match = re.search(r"^package\s+(\w+)", content, re.MULTILINE)
            if not pkg_match:
                continue
            pkg = pkg_match.group(1)

            # Get package path from file path
            pkg_dir = os.path.dirname(rel_path)
            full_pkg = f"{module_name}/{pkg_dir}" if module_name and pkg_dir else pkg

            if package_name and package_name not in full_pkg and package_name != pkg:
                continue

            if full_pkg not in graph:
                graph[full_pkg] = {
                    "package": pkg,
                    "files": [],
                    "imports_internal": [],
                    "imports_external": [],
                    "imported_by": [],
                }
            graph[full_pkg]["files"].append(rel_path)

            # Parse imports
            # Single import: import "path"
            single_imports = re.findall(r'^import\s+"([^"]+)"', content, re.MULTILINE)
            # Multi import: import ( "path1" "path2" )
            multi_import = re.search(r"import\s*\((.*?)\)", content, re.DOTALL)
            all_imports = list(single_imports)
            if multi_import:
                block = multi_import.group(1)
                all_imports.extend(re.findall(r'"([^"]+)"', block))

            for imp in all_imports:
                if module_name and imp.startswith(module_name):
                    if imp not in graph[full_pkg]["imports_internal"]:
                        graph[full_pkg]["imports_internal"].append(imp)
                else:
                    if imp not in graph[full_pkg]["imports_external"]:
                        graph[full_pkg]["imports_external"].append(imp)

        # Build imported_by (reverse map)
        for pkg_path, data in graph.items():
            for internal_dep in data["imports_internal"]:
                if internal_dep in graph:
                    if pkg_path not in graph[internal_dep]["imported_by"]:
                        graph[internal_dep]["imported_by"].append(pkg_path)

        # If package_name specified, filter graph
        if package_name:
            filtered = {}
            for pkg_path, data in graph.items():
                if package_name in pkg_path or package_name == data["package"]:
                    filtered[pkg_path] = data
                    # Also include direct dependencies and dependents
                    for dep in data["imports_internal"]:
                        if dep in graph:
                            filtered[dep] = graph[dep]
                    for dep in data["imported_by"]:
                        if dep in graph:
                            filtered[dep] = graph[dep]
            graph = filtered

        return {
            "status": "success",
            "module": module_name,
            "graph": graph,
            "total_packages": len(graph),
        }

    # ── 12. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware checks before editing a Go symbol.

        Args:
            file_path: Relative file path (e.g., "internal/handlers/user.go")
            symbol_name: Symbol to check (e.g., "CreateUser", "UserService")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read file: {e}"}

        checks: Dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name,
            "symbol_found": symbol_name in content,
            "is_exported": symbol_name[0:1].isupper(),
            "references": [],
            "impact": [],
            "suggestions": [],
            "anti_patterns": [],
        }

        # Find all references across the project
        all_files = self._scan_all_go_files(base)
        for finfo in all_files:
            if finfo["rel_path"] == file_path:
                continue
            if symbol_name in finfo["content"]:
                lines = finfo["content"].split("\n")
                usages = []
                for i, line in enumerate(lines, 1):
                    if symbol_name in line:
                        usages.append({"line": i, "text": line.strip()[:120]})
                if usages:
                    checks["references"].append({
                        "file": finfo["rel_path"],
                        "usages": usages[:5],
                        "total": len(usages),
                    })

        # Context-specific checks
        if "handler" in file_path.lower() or "controller" in file_path.lower():
            checks["impact"].append({
                "type": "handler_change",
                "message": f"Changing handler '{symbol_name}' may affect API behavior",
                "severity": "high",
            })

        if "model" in file_path.lower():
            checks["impact"].append({
                "type": "model_change",
                "message": f"Changing model '{symbol_name}' may require migration updates",
                "severity": "high",
            })

        if "middleware" in file_path.lower():
            checks["impact"].append({
                "type": "middleware_change",
                "message": f"Changing middleware '{symbol_name}' affects all routes using it",
                "severity": "high",
            })

        if "service" in file_path.lower():
            checks["impact"].append({
                "type": "service_change",
                "message": f"Changing service '{symbol_name}' may affect handlers using it",
                "severity": "medium",
            })

        # Check if symbol is part of an interface
        if checks["is_exported"]:
            iface_info = self.get_interface_implementations()
            if iface_info.get("status") == "success":
                for iface_name, iface_data in iface_info.get("interfaces", {}).items():
                    method_names = {m["name"] for m in iface_data.get("methods", [])}
                    if symbol_name in method_names:
                        checks["impact"].append({
                            "type": "interface_method",
                            "message": f"'{symbol_name}' is part of interface '{iface_name}' -- changing signature will break implementations",
                            "severity": "high",
                            "implementations": [
                                impl["type"] for impl in iface_data.get("implementations", [])
                            ],
                        })

        # Anti-pattern check
        for ap in self.ANTI_PATTERNS:
            if re.search(ap["pattern"], content):
                checks["anti_patterns"].append({
                    "pattern": ap["pattern"],
                    "severity": ap["severity"],
                    "message": ap["message"],
                })

        # Check for test files
        test_file = file_path.replace(".go", "_test.go")
        test_path = os.path.join(base, test_file)
        if os.path.isfile(test_path):
            try:
                with open(test_path, "r", encoding="utf-8", errors="replace") as f:
                    test_content = f.read()
                if symbol_name in test_content:
                    checks["suggestions"].append(
                        f"Tests for '{symbol_name}' exist in {test_file} -- update tests after changes"
                    )
            except Exception:
                pass
        else:
            checks["suggestions"].append(
                f"No test file found at {test_file} -- consider adding tests"
            )

        return {
            "status": "success",
            "checks": checks,
            "total_references": sum(r.get("total", 0) for r in checks["references"]),
        }
