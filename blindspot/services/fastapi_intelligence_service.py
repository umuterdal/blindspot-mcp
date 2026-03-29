"""
FastAPI Intelligence Service - FastAPI/Python-specific code analysis.

Provides SQLAlchemy model parsing, Pydantic schema analysis, FastAPI route mapping,
Alembic migration extraction, dependency injection chain tracing, middleware
resolution, and flow tracing for FastAPI applications.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class FastAPIIntelligenceService(BaseService):
    """FastAPI-specific code intelligence for Python web projects."""

    SCAN_DIRS = {
        "models": "app/models",
        "controllers": "app/routers",
        "services": "app/services",
        "middleware": "app/middleware",
        "tests": "tests",
        "schemas": "app/schemas",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bprint\s*\(",
            "severity": "warning",
            "message": "print() in production code -- use logging module instead",
            "file_types": ["py"],
        },
        {
            "pattern": r"time\.sleep\s*\(",
            "severity": "error",
            "message": "Blocking time.sleep() in async handler -- use asyncio.sleep() instead",
            "file_types": ["py"],
        },
        {
            "pattern": r"from\s+\w+\s+import\s+\*",
            "severity": "warning",
            "message": "Wildcard import (import *) -- use explicit imports",
            "file_types": ["py"],
        },
        {
            "pattern": r"""['\"][A-Za-z0-9+/=]{20,}['\"]""",
            "severity": "error",
            "message": "Potential hardcoded secret/token -- use environment variables",
            "file_types": ["py"],
        },
    ]

    PATTERN_REGISTRY = {
        "crud-router": {
            "patterns": [
                r"@(?:app|router)\.\w+\s*\(",
                r"APIRouter\s*\(",
                r"def\s+(?:get|create|update|delete|list)_\w+",
            ],
            "description": "CRUD router endpoint patterns",
        },
        "auth-dependency": {
            "patterns": [
                r"Depends\s*\(\s*\w*[Aa]uth",
                r"OAuth2PasswordBearer",
                r"HTTPBearer",
                r"get_current_user",
                r"jwt\.",
                r"verify_token",
            ],
            "description": "Authentication dependency patterns",
        },
        "pagination": {
            "patterns": [
                r"skip\s*:\s*int",
                r"limit\s*:\s*int",
                r"offset\s*:\s*int",
                r"page\s*:\s*int",
                r"Pagination",
                r"paginate",
            ],
            "description": "Pagination patterns",
        },
        "file-upload": {
            "patterns": [
                r"UploadFile",
                r"File\s*\(",
                r"shutil\.copyfileobj",
                r"aiofiles",
            ],
            "description": "File upload handling patterns",
        },
        "background-task": {
            "patterns": [
                r"BackgroundTasks",
                r"background_tasks\.add_task",
                r"Celery",
                r"celery_app",
                r"\.delay\s*\(",
            ],
            "description": "Background task patterns",
        },
        "websocket": {
            "patterns": [
                r"@(?:app|router)\.websocket\s*\(",
                r"WebSocket",
                r"websocket\.accept",
                r"websocket\.send",
                r"websocket\.receive",
            ],
            "description": "WebSocket endpoint patterns",
        },
        "middleware": {
            "patterns": [
                r"app\.add_middleware\s*\(",
                r"@app\.middleware\s*\(",
                r"BaseHTTPMiddleware",
                r"CORSMiddleware",
                r"TrustedHostMiddleware",
            ],
            "description": "Middleware patterns",
        },
        "exception-handler": {
            "patterns": [
                r"@app\.exception_handler\s*\(",
                r"HTTPException",
                r"RequestValidationError",
                r"raise\s+HTTPException",
            ],
            "description": "Exception handling patterns",
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

    def _scan_files(self, base: str, directory: str, extensions: tuple = (".py",)) -> List[dict]:
        """Scan directory recursively for Python files."""
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", ".venv", "venv", "env")]
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

    def _scan_all_source(self, base: str) -> List[dict]:
        """Scan all known source directories."""
        all_files = []
        seen = set()
        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        # Also scan common Python project directories
        for extra_dir in ["app", "src", "api", "core", "config", "."]:
            for f in self._scan_files(base, extra_dir):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    # ── 1. get_model_schema ───────────────────────────────────────────

    def get_model_schema(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse SQLAlchemy models (Column, relationship(), ForeignKey) and
        Pydantic schemas (BaseModel fields).

        Args:
            model_name: Optional model name filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_models: Dict[str, Any] = {}
        all_files = self._scan_all_source(base)

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # --- SQLAlchemy models ---
            sqla_models = self._parse_sqlalchemy_models(content, rel_path)
            for name, model_info in sqla_models.items():
                if model_name and name.lower() != model_name.lower():
                    continue
                all_models[name] = model_info

            # --- Pydantic schemas ---
            pydantic_models = self._parse_pydantic_models(content, rel_path)
            for name, schema_info in pydantic_models.items():
                if model_name and name.lower() != model_name.lower():
                    continue
                # If same name exists from SQLAlchemy, merge schema info
                if name in all_models:
                    all_models[name]["pydantic_schema"] = schema_info
                else:
                    all_models[name] = schema_info

        if model_name and not all_models:
            return {"status": "error", "message": f"Model '{model_name}' not found"}

        return {
            "status": "success",
            "models": all_models,
            "total_models": len(all_models),
        }

    def _parse_sqlalchemy_models(self, content: str, rel_path: str) -> Dict[str, Any]:
        """Parse SQLAlchemy model definitions."""
        models: Dict[str, Any] = {}

        # class User(Base): or class User(db.Model): or class User(DeclarativeBase):
        class_pattern = re.compile(
            r"class\s+(\w+)\s*\(\s*(?:Base|db\.Model|DeclarativeBase|(?:\w+\.)*Model)\s*\)\s*:",
            re.MULTILINE,
        )

        for match in class_pattern.finditer(content):
            name = match.group(1)
            # Extract class body (indented block after class definition)
            class_body = self._extract_python_class_body(content, match.end())

            columns = []
            relationships = []
            table_name = None

            # __tablename__
            tablename_match = re.search(
                r"__tablename__\s*=\s*['\"](\w+)['\"]",
                class_body,
            )
            if tablename_match:
                table_name = tablename_match.group(1)

            # Column definitions: field = Column(Type, ...)
            col_pattern = re.compile(
                r"(\w+)\s*=\s*(?:mapped_column|Column)\s*\(\s*(?:(\w+(?:\.\w+)?)\s*(?:\([^)]*\))?\s*,?\s*)?(.*?)\)",
                re.DOTALL,
            )
            for col_match in col_pattern.finditer(class_body):
                col_name = col_match.group(1)
                col_type = col_match.group(2) or "unknown"
                col_options = col_match.group(3) or ""

                col_info: Dict[str, Any] = {
                    "name": col_name,
                    "type": col_type,
                }

                if "primary_key=True" in col_options:
                    col_info["primary_key"] = True
                if "nullable=False" in col_options:
                    col_info["nullable"] = False
                if "unique=True" in col_options:
                    col_info["unique"] = True
                if "index=True" in col_options:
                    col_info["indexed"] = True

                # ForeignKey
                fk_match = re.search(r"ForeignKey\s*\(\s*['\"]([^'\"]+)['\"]", col_options)
                if fk_match:
                    col_info["foreign_key"] = fk_match.group(1)

                # Default
                default_match = re.search(r"default\s*=\s*([^,)]+)", col_options)
                if default_match:
                    col_info["default"] = default_match.group(1).strip()

                # server_default
                server_default_match = re.search(r"server_default\s*=\s*([^,)]+)", col_options)
                if server_default_match:
                    col_info["server_default"] = server_default_match.group(1).strip()

                columns.append(col_info)

            # Mapped columns (SQLAlchemy 2.0 style): field: Mapped[str] = mapped_column(...)
            mapped_pattern = re.compile(
                r"(\w+)\s*:\s*Mapped\[([^\]]+)\]\s*=\s*mapped_column\s*\(([^)]*)\)",
            )
            for mm in mapped_pattern.finditer(class_body):
                col_name = mm.group(1)
                # Skip if already found
                if any(c["name"] == col_name for c in columns):
                    continue
                col_type = mm.group(2).strip()
                col_options = mm.group(3)

                col_info = {"name": col_name, "type": col_type}
                if "primary_key=True" in col_options:
                    col_info["primary_key"] = True
                if "nullable=False" in col_options:
                    col_info["nullable"] = False

                fk_match = re.search(r"ForeignKey\s*\(\s*['\"]([^'\"]+)['\"]", col_options)
                if fk_match:
                    col_info["foreign_key"] = fk_match.group(1)

                columns.append(col_info)

            # Relationship definitions
            rel_pattern = re.compile(
                r"(\w+)\s*=\s*relationship\s*\(\s*['\"]?(\w+)['\"]?\s*(?:,\s*(.+?))?\s*\)",
                re.DOTALL,
            )
            for rel_match in rel_pattern.finditer(class_body):
                rel_info: Dict[str, Any] = {
                    "name": rel_match.group(1),
                    "target": rel_match.group(2),
                }
                options = rel_match.group(3) or ""

                back_populates = re.search(r"back_populates\s*=\s*['\"](\w+)['\"]", options)
                if back_populates:
                    rel_info["back_populates"] = back_populates.group(1)

                backref = re.search(r"backref\s*=\s*['\"](\w+)['\"]", options)
                if backref:
                    rel_info["backref"] = backref.group(1)

                if "uselist=False" in options:
                    rel_info["type"] = "one_to_one"
                elif "secondary=" in options:
                    rel_info["type"] = "many_to_many"
                    sec_match = re.search(r"secondary\s*=\s*['\"]?(\w+)['\"]?", options)
                    if sec_match:
                        rel_info["secondary_table"] = sec_match.group(1)
                else:
                    rel_info["type"] = "one_to_many"

                if "lazy=" in options:
                    lazy_match = re.search(r"lazy\s*=\s*['\"]?(\w+)['\"]?", options)
                    if lazy_match:
                        rel_info["lazy"] = lazy_match.group(1)

                if "cascade=" in options:
                    cascade_match = re.search(r"cascade\s*=\s*['\"]([^'\"]+)['\"]", options)
                    if cascade_match:
                        rel_info["cascade"] = cascade_match.group(1)

                relationships.append(rel_info)

            # Mapped relationship (2.0 style)
            mapped_rel_pattern = re.compile(
                r"(\w+)\s*:\s*Mapped\[(?:List\[)?['\"]?(\w+)['\"]?\]?\]\s*=\s*relationship\s*\(([^)]*)\)",
            )
            for mm in mapped_rel_pattern.finditer(class_body):
                rel_name = mm.group(1)
                if any(r["name"] == rel_name for r in relationships):
                    continue
                rel_info = {
                    "name": rel_name,
                    "target": mm.group(2),
                    "type": "one_to_many" if "List[" in mm.group(0) else "one_to_one",
                }
                relationships.append(rel_info)

            models[name] = {
                "file": rel_path,
                "orm": "sqlalchemy",
                "table_name": table_name,
                "columns": columns,
                "relationships": relationships,
            }

        return models

    def _parse_pydantic_models(self, content: str, rel_path: str) -> Dict[str, Any]:
        """Parse Pydantic model/schema definitions."""
        models: Dict[str, Any] = {}

        # class UserCreate(BaseModel): or class UserResponse(BaseSchema):
        class_pattern = re.compile(
            r"class\s+(\w+)\s*\(\s*(?:BaseModel|BaseSchema|(?:\w+\.)*BaseModel)\s*\)\s*:",
            re.MULTILINE,
        )

        for match in class_pattern.finditer(content):
            name = match.group(1)
            class_body = self._extract_python_class_body(content, match.end())

            fields = []

            # Pydantic field: field_name: type = Field(...) or field_name: type
            field_pattern = re.compile(
                r"(\w+)\s*:\s*(?:Optional\[)?(\w+(?:\[[\w, ]+\])?)(?:\])?\s*(?:=\s*(.+?))?(?:\n|\Z)",
            )
            for fm in field_pattern.finditer(class_body):
                field_name = fm.group(1)
                field_type = fm.group(2)
                default_str = fm.group(3) or ""

                if field_name.startswith("_") or field_name in ("model_config", "Config"):
                    continue

                field_info: Dict[str, Any] = {
                    "name": field_name,
                    "type": field_type,
                }

                if "Optional" in fm.group(0):
                    field_info["optional"] = True

                if "Field(" in default_str:
                    # Extract Field() parameters
                    desc_match = re.search(r"description\s*=\s*['\"]([^'\"]+)['\"]", default_str)
                    if desc_match:
                        field_info["description"] = desc_match.group(1)
                    min_match = re.search(r"(?:min_length|ge|gt)\s*=\s*(\d+)", default_str)
                    if min_match:
                        field_info["min"] = int(min_match.group(1))
                    max_match = re.search(r"(?:max_length|le|lt)\s*=\s*(\d+)", default_str)
                    if max_match:
                        field_info["max"] = int(max_match.group(1))
                    if "..." in default_str:
                        field_info["required"] = True
                elif default_str.strip() == "None":
                    field_info["default"] = None
                elif default_str.strip() == "...":
                    field_info["required"] = True
                elif default_str.strip():
                    field_info["default"] = default_str.strip()

                fields.append(field_info)

            # Config class
            config = {}
            config_match = re.search(r"class\s+Config\s*:", class_body)
            if config_match:
                config_body = self._extract_python_class_body(class_body, config_match.end())
                if "from_attributes = True" in config_body or "orm_mode = True" in config_body:
                    config["orm_mode"] = True

            # model_config
            mc_match = re.search(r"model_config\s*=\s*ConfigDict\s*\(([^)]+)\)", class_body)
            if mc_match:
                if "from_attributes=True" in mc_match.group(1):
                    config["orm_mode"] = True

            models[name] = {
                "file": rel_path,
                "type": "pydantic_schema",
                "fields": fields,
                "config": config,
            }

        return models

    def _extract_python_class_body(self, content: str, start: int) -> str:
        """Extract a Python class body (indented block) starting after the colon."""
        lines = content[start:].split("\n")
        body_lines = []
        base_indent = None

        for line in lines[1:]:  # Skip the first line (class def continuation)
            if not line.strip():
                body_lines.append("")
                continue

            indent = len(line) - len(line.lstrip())
            if base_indent is None:
                if line.strip():
                    base_indent = indent
            elif indent < base_indent and line.strip():
                break

            body_lines.append(line)

        return "\n".join(body_lines)

    # ── 2. get_route_map ──────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse FastAPI route definitions: @app.get/post/put/delete, @router.get/post,
        APIRouter(prefix=). Also supports Flask @app.route.

        Args:
            filter_prefix: Optional URL prefix filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        routes: List[Dict[str, Any]] = []

        # FastAPI decorators: @app.get("/path"), @router.post("/path", ...)
        fastapi_route_pattern = re.compile(
            r"@(\w+)\.(get|post|put|patch|delete|options|head)\s*\(\s*['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*(.+?))?\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)",
            re.MULTILINE,
        )

        # Flask route: @app.route("/path", methods=["GET", "POST"])
        flask_route_pattern = re.compile(
            r"@(\w+)\.route\s*\(\s*['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?"
            r"(?:\s*,.+?)?\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)",
            re.MULTILINE,
        )

        # APIRouter prefix detection
        router_prefix_map: Dict[str, str] = {}
        router_prefix_pattern = re.compile(
            r"(\w+)\s*=\s*APIRouter\s*\(\s*(?:.*?prefix\s*=\s*['\"]([^'\"]+)['\"])?",
            re.DOTALL,
        )

        # app.include_router(router, prefix="/path")
        include_router_pattern = re.compile(
            r"(?:app|main_app)\s*\.include_router\s*\(\s*(\w+(?:\.\w+)?)\s*"
            r"(?:,\s*prefix\s*=\s*['\"]([^'\"]+)['\"])?",
        )

        # Build router prefix map
        for finfo in all_files:
            content = finfo["content"]

            # Local APIRouter definitions
            for m in router_prefix_pattern.finditer(content):
                router_var = m.group(1)
                prefix = m.group(2) or ""
                router_prefix_map[router_var] = prefix

            # include_router with prefix
            for m in include_router_pattern.finditer(content):
                router_ref = m.group(1)
                prefix = m.group(2) or ""
                # Map the router reference to its include prefix
                router_name = router_ref.split(".")[-1] if "." in router_ref else router_ref
                if router_name in router_prefix_map:
                    existing = router_prefix_map[router_name]
                    router_prefix_map[router_name] = prefix + existing
                else:
                    router_prefix_map[router_name] = prefix

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # FastAPI routes
            for match in fastapi_route_pattern.finditer(content):
                router_var = match.group(1)
                method = match.group(2).upper()
                path = match.group(3)
                options_str = match.group(4) or ""
                handler_name = match.group(5)

                # Resolve router prefix
                prefix = router_prefix_map.get(router_var, "")
                full_path = prefix + path

                route_info: Dict[str, Any] = {
                    "method": method,
                    "path": full_path,
                    "handler": handler_name,
                    "file": rel_path,
                    "framework": "fastapi",
                }

                # Extract response_model
                resp_model_match = re.search(r"response_model\s*=\s*(\w+(?:\[[\w, ]+\])?)", options_str)
                if resp_model_match:
                    route_info["response_model"] = resp_model_match.group(1)

                # Extract status_code
                status_match = re.search(r"status_code\s*=\s*(\d+)", options_str)
                if status_match:
                    route_info["status_code"] = int(status_match.group(1))

                # Extract dependencies
                deps_match = re.search(r"dependencies\s*=\s*\[([^\]]+)\]", options_str)
                if deps_match:
                    deps = re.findall(r"Depends\s*\(\s*(\w+)\s*\)", deps_match.group(1))
                    route_info["dependencies"] = deps

                # Extract tags
                tags_match = re.search(r"tags\s*=\s*\[([^\]]+)\]", options_str)
                if tags_match:
                    tags = re.findall(r"['\"](\w+)['\"]", tags_match.group(1))
                    route_info["tags"] = tags

                routes.append(route_info)

            # Flask routes
            for match in flask_route_pattern.finditer(content):
                router_var = match.group(1)
                path = match.group(2)
                methods_str = match.group(3) or '"GET"'
                handler_name = match.group(4)

                methods = re.findall(r"['\"](\w+)['\"]", methods_str)

                for method in methods:
                    routes.append({
                        "method": method.upper(),
                        "path": path,
                        "handler": handler_name,
                        "file": rel_path,
                        "framework": "flask",
                    })

        # Deduplicate
        seen: Set[Tuple[str, str, str]] = set()
        unique_routes = []
        for r in routes:
            key = (r["method"], r["path"], r["handler"])
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

    # ── 3. get_migration_schema ───────────────────────────────────────

    def get_migration_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Alembic migrations (op.create_table, op.add_column, etc).

        Args:
            table_name: Optional table name filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        tables: Dict[str, Any] = {}

        # Find Alembic versions directory
        migration_dirs = [
            "alembic/versions",
            "migrations/versions",
            "app/alembic/versions",
            "db/versions",
        ]

        found_dir = None
        for mdir in migration_dirs:
            full = os.path.join(base, mdir)
            if os.path.isdir(full):
                found_dir = mdir
                break

        if not found_dir:
            return {"status": "error", "message": "No Alembic migrations directory found"}

        migration_files = self._scan_files(base, found_dir)

        for finfo in sorted(migration_files, key=lambda x: x["rel_path"]):
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # op.create_table('table_name', ...)
            create_pattern = re.compile(
                r"op\.create_table\s*\(\s*['\"](\w+)['\"]\s*,",
                re.MULTILINE,
            )
            for match in create_pattern.finditer(content):
                tname = match.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue

                # Find the closing paren for create_table
                paren_start = match.end() - 1
                block = self._extract_paren_block(content, paren_start)

                columns = self._parse_alembic_columns(block)

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                tables[tname]["columns"].extend(columns)
                tables[tname]["files"].append(rel_path)

            # op.add_column('table', sa.Column('name', sa.Type()))
            add_col_pattern = re.compile(
                r"op\.add_column\s*\(\s*['\"](\w+)['\"]\s*,\s*sa\.Column\s*\(\s*['\"](\w+)['\"]\s*,\s*sa\.(\w+)",
            )
            for match in add_col_pattern.finditer(content):
                tname = match.group(1)
                col_name = match.group(2)
                col_type = match.group(3)

                if table_name and tname.lower() != table_name.lower():
                    continue

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                tables[tname]["columns"].append({
                    "name": col_name,
                    "type": col_type,
                    "operation": "add_column",
                    "migration_file": rel_path,
                })

            # op.create_index('idx_name', 'table', ['col1', 'col2'])
            create_idx_pattern = re.compile(
                r"op\.create_index\s*\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*,\s*\[([^\]]+)\]"
                r"(?:\s*,\s*unique\s*=\s*(True|False))?",
            )
            for match in create_idx_pattern.finditer(content):
                idx_name = match.group(1)
                tname = match.group(2)
                cols_str = match.group(3)
                unique = match.group(4) == "True" if match.group(4) else False

                if table_name and tname.lower() != table_name.lower():
                    continue

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                cols = re.findall(r"['\"](\w+)['\"]", cols_str)
                tables[tname]["indexes"].append({
                    "name": idx_name,
                    "columns": cols,
                    "unique": unique,
                    "migration_file": rel_path,
                })

            # op.create_foreign_key(...)
            fk_pattern = re.compile(
                r"op\.create_foreign_key\s*\(\s*['\"]?(\w+)['\"]?\s*,\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*,\s*\[([^\]]+)\]\s*,\s*\[([^\]]+)\]",
            )
            for match in fk_pattern.finditer(content):
                tname = match.group(2)
                if table_name and tname.lower() != table_name.lower():
                    continue

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                local_cols = re.findall(r"['\"](\w+)['\"]", match.group(4))
                remote_cols = re.findall(r"['\"](\w+)['\"]", match.group(5))

                tables[tname]["foreign_keys"].append({
                    "name": match.group(1),
                    "local_columns": local_cols,
                    "referred_table": match.group(3),
                    "referred_columns": remote_cols,
                    "migration_file": rel_path,
                })

        if table_name and not tables:
            return {"status": "error", "message": f"Table '{table_name}' not found in migrations"}

        return {
            "status": "success",
            "tables": tables,
            "total_tables": len(tables),
        }

    def _extract_paren_block(self, content: str, start: int) -> str:
        """Extract content inside matching parentheses."""
        if start >= len(content):
            return ""
        # Find the opening paren
        pos = start
        while pos < len(content) and content[pos] != "(":
            pos += 1
        if pos >= len(content):
            return ""

        depth = 0
        paren_start = pos
        while pos < len(content):
            if content[pos] == "(":
                depth += 1
            elif content[pos] == ")":
                depth -= 1
                if depth == 0:
                    return content[paren_start + 1:pos]
            pos += 1
        return content[paren_start + 1:]

    def _parse_alembic_columns(self, block: str) -> List[Dict[str, Any]]:
        """Parse column definitions from an Alembic create_table block."""
        columns = []

        # sa.Column('name', sa.Type(), ...)
        col_pattern = re.compile(
            r"sa\.Column\s*\(\s*['\"](\w+)['\"]\s*,\s*sa\.(\w+)(?:\([^)]*\))?\s*(?:,\s*(.+?))?\s*\)",
            re.DOTALL,
        )
        for m in col_pattern.finditer(block):
            col_info: Dict[str, Any] = {
                "name": m.group(1),
                "type": m.group(2),
            }
            options = m.group(3) or ""

            if "primary_key=True" in options:
                col_info["primary_key"] = True
            if "nullable=False" in options:
                col_info["nullable"] = False
            if "unique=True" in options:
                col_info["unique"] = True

            fk_match = re.search(r"sa\.ForeignKey\s*\(\s*['\"]([^'\"]+)['\"]", options)
            if fk_match:
                col_info["foreign_key"] = fk_match.group(1)

            server_default = re.search(r"server_default\s*=\s*([^,)]+)", options)
            if server_default:
                col_info["server_default"] = server_default.group(1).strip()

            columns.append(col_info)

        return columns

    # ── 4. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> Depends() -> handler -> service -> CRUD -> SQLAlchemy query -> response.

        Args:
            entry_point: Handler name or route path.
            method: Optional HTTP method.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
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
                    "handler": route.get("handler"),
                    "file": route.get("file"),
                },
                "dependencies": [],
                "handler_params": [],
                "service_calls": [],
                "db_operations": [],
                "response_model": route.get("response_model"),
            }

            handler_name = route.get("handler", "")

            for finfo in all_files:
                content = finfo["content"]

                # Find handler function
                handler_match = re.search(
                    rf"(?:async\s+)?def\s+{re.escape(handler_name)}\s*\(([^)]*)\)",
                    content,
                    re.DOTALL,
                )
                if not handler_match:
                    continue

                params_str = handler_match.group(1)

                # Extract Depends() dependencies
                depends_pattern = re.compile(
                    r"(\w+)\s*(?::\s*\w+)?\s*=\s*Depends\s*\(\s*(\w+)\s*\)",
                )
                for dm in depends_pattern.finditer(params_str):
                    flow["dependencies"].append({
                        "param": dm.group(1),
                        "dependency": dm.group(2),
                    })

                # Extract other handler parameters
                param_pattern = re.compile(
                    r"(\w+)\s*:\s*([\w\[\], ]+?)(?:\s*=\s*([^,]+))?\s*(?:,|$)",
                )
                for pm in param_pattern.finditer(params_str):
                    param_name = pm.group(1)
                    param_type = pm.group(2).strip()
                    if "Depends" not in (pm.group(3) or ""):
                        flow["handler_params"].append({
                            "name": param_name,
                            "type": param_type,
                        })

                # Find function body
                func_body = self._extract_python_class_body(content, handler_match.end())

                # Service calls
                service_calls = re.findall(
                    r"(?:\w+)\.(\w+(?:_service|Service))\s*\.\s*(\w+)\s*\(",
                    func_body,
                )
                # Also: service.method() where service is a parameter
                direct_service = re.findall(
                    r"(\w+(?:_service|_crud|_repo))\s*\.\s*(\w+)\s*\(",
                    func_body,
                )
                flow["service_calls"] = [
                    {"service": s, "method": m}
                    for s, m in list(set(service_calls + direct_service))
                ]

                # DB operations
                db_ops = re.findall(
                    r"(?:db|session)\s*\.\s*(query|add|delete|commit|rollback|refresh|execute|scalar|scalars)\s*\(",
                    func_body,
                )
                flow["db_operations"] = list(set(db_ops))

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
        Parse app.add_middleware(), @app.middleware("http"), and FastAPI dependencies.

        Args:
            route_path: Optional route path for context.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        middleware_entries: List[Dict[str, Any]] = []
        order = 0

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # app.add_middleware(CORSMiddleware, ...)
            add_mw_pattern = re.compile(
                r"(?:app|application)\s*\.add_middleware\s*\(\s*(\w+)(?:\s*,\s*(.+?))?\s*\)",
                re.DOTALL,
            )
            for match in add_mw_pattern.finditer(content):
                order += 1
                mw_info: Dict[str, Any] = {
                    "order": order,
                    "name": match.group(1),
                    "type": "add_middleware",
                    "file": rel_path,
                }
                options = match.group(2)
                if options:
                    # Extract key=value pairs
                    opts = re.findall(r"(\w+)\s*=\s*([^,]+)", options)
                    mw_info["options"] = {k.strip(): v.strip() for k, v in opts}
                middleware_entries.append(mw_info)

            # @app.middleware("http")
            decorator_mw = re.compile(
                r"@(\w+)\.middleware\s*\(\s*['\"](\w+)['\"]\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)",
            )
            for match in decorator_mw.finditer(content):
                order += 1
                middleware_entries.append({
                    "order": order,
                    "name": match.group(3),
                    "type": f"{match.group(2)}_middleware",
                    "file": rel_path,
                })

            # BaseHTTPMiddleware subclass
            base_mw = re.compile(
                r"class\s+(\w+)\s*\(\s*BaseHTTPMiddleware\s*\)",
            )
            for match in base_mw.finditer(content):
                order += 1
                middleware_entries.append({
                    "order": order,
                    "name": match.group(1),
                    "type": "BaseHTTPMiddleware",
                    "file": rel_path,
                })

        return {
            "status": "success",
            "middleware": middleware_entries,
            "total": len(middleware_entries),
            "route_filter": route_path,
        }

    # ── 6. get_validation_chain ───────────────────────────────────────

    def get_validation_chain(self, route_path: str) -> Dict[str, Any]:
        """
        Trace: Pydantic request model -> FastAPI handler param -> response model.
        Detect field mismatches.

        Args:
            route_path: Route path to trace.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        validation_info: Dict[str, Any] = {
            "route": route_path,
            "request_model": None,
            "request_fields": [],
            "response_model": None,
            "response_fields": [],
            "handler_params": [],
            "mismatches": [],
        }

        # Find matching route
        route_map = self.get_route_map()
        matching_routes = [
            r for r in route_map.get("routes", [])
            if route_path in r["path"]
        ]

        if not matching_routes:
            return {"status": "error", "message": f"No routes matching '{route_path}'"}

        route = matching_routes[0]
        all_files = self._scan_all_source(base)

        # Find handler function
        handler_name = route.get("handler", "")
        for finfo in all_files:
            content = finfo["content"]

            handler_match = re.search(
                rf"(?:async\s+)?def\s+{re.escape(handler_name)}\s*\(([^)]*)\)",
                content,
                re.DOTALL,
            )
            if not handler_match:
                continue

            params_str = handler_match.group(1)

            # Find Pydantic model parameters (request body)
            param_pattern = re.compile(
                r"(\w+)\s*:\s*(\w+)\s*(?:=\s*[^,]+)?\s*(?:,|$)",
            )
            for pm in param_pattern.finditer(params_str):
                param_name = pm.group(1)
                param_type = pm.group(2)

                validation_info["handler_params"].append({
                    "name": param_name,
                    "type": param_type,
                })

                # Check if it's a Pydantic model
                if param_type[0].isupper() and param_type not in (
                    "Request", "Response", "Session", "Depends", "Query",
                    "Path", "Body", "Header", "Cookie", "Form", "File",
                ):
                    validation_info["request_model"] = param_type

            break

        # Get request model fields
        if validation_info["request_model"]:
            model_info = self.get_model_schema(validation_info["request_model"])
            if model_info.get("status") == "success":
                model_data = model_info.get("models", {}).get(validation_info["request_model"], {})
                fields = model_data.get("fields", [])
                validation_info["request_fields"] = fields

        # Get response model fields
        response_model = route.get("response_model")
        if response_model:
            validation_info["response_model"] = response_model
            # Strip List[] wrapper
            clean_model = re.sub(r"^(?:List|list)\[(.+)\]$", r"\1", response_model)
            model_info = self.get_model_schema(clean_model)
            if model_info.get("status") == "success":
                model_data = model_info.get("models", {}).get(clean_model, {})
                fields = model_data.get("fields", [])
                validation_info["response_fields"] = fields

        # Detect mismatches
        request_field_names = {f["name"] for f in validation_info["request_fields"]}
        response_field_names = {f["name"] for f in validation_info["response_fields"]}

        if request_field_names and response_field_names:
            only_in_request = request_field_names - response_field_names
            only_in_response = response_field_names - request_field_names

            if only_in_request:
                validation_info["mismatches"].append({
                    "type": "request_only_fields",
                    "fields": sorted(only_in_request),
                    "severity": "info",
                    "message": "Fields in request model but not in response model",
                })
            if only_in_response:
                validation_info["mismatches"].append({
                    "type": "response_only_fields",
                    "fields": sorted(only_in_response),
                    "severity": "info",
                    "message": "Fields in response model but not in request model (likely computed/auto-generated)",
                })

        return {
            "status": "success",
            "validation": validation_info,
        }

    # ── 7. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify: route exists, handler params, auth dependencies, response model.

        Args:
            method: HTTP method
            url: URL path
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
                if route["method"].upper() == method.upper() and self._fastapi_paths_match(route["path"], url):
                    route_found = True
                    matched_route = route
                    break

        if route_found:
            verification["checks"].append({
                "check": "route_registered",
                "status": "pass",
                "detail": f"Route found: {matched_route.get('handler')} in {matched_route.get('file')}",
            })
        else:
            verification["errors"].append({
                "check": "route_registered",
                "status": "fail",
                "detail": f"Route {method.upper()} {url} not found",
            })
            return {"status": "success", "verification": verification}

        # 2. Check handler implementation
        handler_name = matched_route.get("handler", "")
        all_files = self._scan_all_source(base)
        handler_found = False

        for finfo in all_files:
            handler_match = re.search(
                rf"(?:async\s+)?def\s+{re.escape(handler_name)}\s*\(([^)]*)\)",
                finfo["content"],
                re.DOTALL,
            )
            if not handler_match:
                continue

            handler_found = True
            params_str = handler_match.group(1)

            verification["checks"].append({
                "check": "handler_exists",
                "status": "pass",
                "detail": f"Handler '{handler_name}' found in {finfo['rel_path']}",
            })

            # Check for auth dependencies
            if "Depends" in params_str:
                auth_deps = re.findall(
                    r"Depends\s*\(\s*(\w*(?:auth|current_user|token|verify)\w*)\s*\)",
                    params_str,
                    re.IGNORECASE,
                )
                if auth_deps:
                    verification["checks"].append({
                        "check": "auth_dependency",
                        "status": "pass",
                        "detail": f"Auth dependencies: {', '.join(auth_deps)}",
                    })
                else:
                    verification["warnings"].append({
                        "check": "auth_dependency",
                        "status": "warning",
                        "detail": "No auth-related dependencies found (Depends exists but no auth pattern)",
                    })
            elif method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                verification["warnings"].append({
                    "check": "auth_dependency",
                    "status": "warning",
                    "detail": "No Depends() found for write endpoint -- may need authentication",
                })

            # Check response model
            if matched_route.get("response_model"):
                verification["checks"].append({
                    "check": "response_model",
                    "status": "pass",
                    "detail": f"Response model: {matched_route['response_model']}",
                })
            else:
                verification["warnings"].append({
                    "check": "response_model",
                    "status": "warning",
                    "detail": "No response_model defined -- consider adding for documentation",
                })

            # Check for async
            is_async = "async def" in finfo["content"][handler_match.start():handler_match.start() + 100]
            verification["checks"].append({
                "check": "async_handler",
                "status": "pass" if is_async else "info",
                "detail": f"Handler is {'async' if is_async else 'sync'}",
            })

            break

        if not handler_found:
            verification["errors"].append({
                "check": "handler_exists",
                "status": "fail",
                "detail": f"Handler '{handler_name}' not found in source files",
            })

        return {
            "status": "success",
            "verification": verification,
        }

    def _fastapi_paths_match(self, pattern: str, url: str) -> bool:
        """Check if a FastAPI route pattern matches a URL."""
        regex = re.sub(r"\{(\w+)\}", r"[^/]+", pattern)
        regex = "^" + regex + "$"
        try:
            return bool(re.match(regex, url))
        except re.error:
            return pattern == url

    # ── 8. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the FastAPI project.

        Args:
            pattern_type: One of: "naming", "dependency-injection", "orm", "testing", "async"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_types = ["naming", "dependency-injection", "orm", "testing", "async"]
        if pattern_type not in valid_types:
            return {"status": "error", "message": f"Invalid pattern_type. Choose from: {valid_types}"}

        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}
        all_files = self._scan_all_source(base)

        if pattern_type == "naming":
            file_patterns: Dict[str, int] = {}
            class_styles: Dict[str, int] = {}

            for finfo in all_files:
                fname = os.path.basename(finfo["rel_path"])
                # File naming
                if re.match(r"^[a-z_]+\.py$", fname):
                    file_patterns["snake_case"] = file_patterns.get("snake_case", 0) + 1
                elif re.match(r"^[a-z]+-[a-z]+\.py$", fname):
                    file_patterns["kebab-case"] = file_patterns.get("kebab-case", 0) + 1

                # Class naming
                classes = re.findall(r"class\s+(\w+)", finfo["content"])
                for cls in classes:
                    if re.match(r"^[A-Z][a-zA-Z]+$", cls):
                        class_styles["PascalCase"] = class_styles.get("PascalCase", 0) + 1

                # Function naming
                funcs = re.findall(r"def\s+(\w+)", finfo["content"])
                for func in funcs:
                    if re.match(r"^[a-z_][a-z0-9_]*$", func):
                        class_styles["snake_case_funcs"] = class_styles.get("snake_case_funcs", 0) + 1

            conventions["findings"].append({
                "category": "file_naming",
                "patterns": file_patterns,
            })
            conventions["findings"].append({
                "category": "code_naming",
                "patterns": class_styles,
            })

        elif pattern_type == "dependency-injection":
            depends_patterns: Dict[str, int] = {}
            depends_examples: List[Dict[str, Any]] = []

            for finfo in all_files:
                content = finfo["content"]
                depends_matches = re.findall(r"Depends\s*\(\s*(\w+)\s*\)", content)
                for dep in depends_matches:
                    depends_patterns[dep] = depends_patterns.get(dep, 0) + 1
                    if len(depends_examples) < 20:
                        depends_examples.append({
                            "dependency": dep,
                            "file": finfo["rel_path"],
                        })

            conventions["findings"].append({
                "category": "dependency_injection",
                "dependencies": depends_patterns,
                "examples": depends_examples[:10],
                "total_depends_calls": sum(depends_patterns.values()),
            })

        elif pattern_type == "orm":
            orm_patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if "db.query(" in content or "session.query(" in content:
                    orm_patterns["query_style"] = orm_patterns.get("query_style", 0) + 1
                if "select(" in content and "from sqlalchemy" in content:
                    orm_patterns["select_style_2.0"] = orm_patterns.get("select_style_2.0", 0) + 1
                if ".filter(" in content:
                    orm_patterns["filter"] = orm_patterns.get("filter", 0) + content.count(".filter(")
                if ".filter_by(" in content:
                    orm_patterns["filter_by"] = orm_patterns.get("filter_by", 0) + content.count(".filter_by(")
                if ".join(" in content:
                    orm_patterns["joins"] = orm_patterns.get("joins", 0) + 1
                if ".options(" in content:
                    orm_patterns["eager_loading"] = orm_patterns.get("eager_loading", 0) + 1

            conventions["findings"].append({
                "category": "orm_patterns",
                "patterns": orm_patterns,
            })

        elif pattern_type == "testing":
            test_patterns: Dict[str, int] = {}
            test_files = self._scan_files(base, self.SCAN_DIRS["tests"])
            if not test_files:
                for d in ["test", "tests", "spec"]:
                    test_files.extend(self._scan_files(base, d))

            for finfo in test_files:
                content = finfo["content"]
                if "def test_" in content:
                    test_patterns["test_functions"] = test_patterns.get("test_functions", 0) + content.count("def test_")
                if "TestClient" in content:
                    test_patterns["test_client"] = test_patterns.get("test_client", 0) + 1
                if "AsyncClient" in content or "httpx.AsyncClient" in content:
                    test_patterns["async_test_client"] = test_patterns.get("async_test_client", 0) + 1
                if "pytest.fixture" in content or "@fixture" in content:
                    test_patterns["fixtures"] = test_patterns.get("fixtures", 0) + 1
                if "mock" in content.lower() or "Mock" in content:
                    test_patterns["mocking"] = test_patterns.get("mocking", 0) + 1
                if "parametrize" in content:
                    test_patterns["parametrize"] = test_patterns.get("parametrize", 0) + 1
                if "conftest" in finfo["rel_path"]:
                    test_patterns["conftest_files"] = test_patterns.get("conftest_files", 0) + 1

            conventions["findings"].append({
                "category": "testing",
                "patterns": test_patterns,
                "test_files_found": len(test_files),
            })

        elif pattern_type == "async":
            async_patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                async_funcs = len(re.findall(r"async\s+def\s+", content))
                sync_funcs = len(re.findall(r"(?<!async\s)def\s+\w+", content))
                if async_funcs:
                    async_patterns["async_functions"] = async_patterns.get("async_functions", 0) + async_funcs
                if sync_funcs:
                    async_patterns["sync_functions"] = async_patterns.get("sync_functions", 0) + sync_funcs
                if "await" in content:
                    async_patterns["await_calls"] = async_patterns.get("await_calls", 0) + content.count("await ")
                if "asyncio.gather" in content:
                    async_patterns["gather_calls"] = async_patterns.get("gather_calls", 0) + 1
                if "asyncio.create_task" in content:
                    async_patterns["create_task"] = async_patterns.get("create_task", 0) + 1
                if "run_in_executor" in content:
                    async_patterns["executor_calls"] = async_patterns.get("executor_calls", 0) + 1

            conventions["findings"].append({
                "category": "async_patterns",
                "patterns": async_patterns,
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
            description: Natural language description.
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

        all_files = self._scan_all_source(base)

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
        Generate a FastAPI project overview: routers, models, schemas,
        dependencies, middleware, background tasks.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "structure": {},
            "routes_summary": None,
            "models_summary": None,
            "middleware_summary": None,
            "dependencies": {},
            "python_version": None,
        }

        # Directory structure
        for category, directory in self.SCAN_DIRS.items():
            full_dir = os.path.join(base, directory)
            if os.path.isdir(full_dir):
                file_count = 0
                for root, dirs, files in os.walk(full_dir):
                    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "venv")]
                    file_count += len([f for f in files if f.endswith(".py")])
                snapshot["structure"][category] = {
                    "path": directory,
                    "file_count": file_count,
                }

        # Routes summary
        route_map = self.get_route_map()
        if route_map.get("status") == "success":
            routes = route_map.get("routes", [])
            method_counts: Dict[str, int] = {}
            tags: Set[str] = set()
            for r in routes:
                m = r["method"]
                method_counts[m] = method_counts.get(m, 0) + 1
                for tag in r.get("tags", []):
                    tags.add(tag)
            snapshot["routes_summary"] = {
                "total": len(routes),
                "by_method": method_counts,
                "tags": sorted(tags),
            }

        # Models summary
        models = self.get_model_schema()
        if models.get("status") == "success":
            model_names = list(models.get("models", {}).keys())
            sqlalchemy_models = [
                n for n, d in models.get("models", {}).items()
                if d.get("orm") == "sqlalchemy"
            ]
            pydantic_schemas = [
                n for n, d in models.get("models", {}).items()
                if d.get("type") == "pydantic_schema"
            ]
            snapshot["models_summary"] = {
                "total": len(model_names),
                "sqlalchemy_models": sqlalchemy_models,
                "pydantic_schemas": pydantic_schemas,
            }

        # Middleware summary
        middleware = self.get_middleware_chain()
        if middleware.get("status") == "success":
            snapshot["middleware_summary"] = {
                "total": middleware.get("total", 0),
                "middleware": [m["name"] for m in middleware.get("middleware", [])],
            }

        # Requirements / pyproject.toml
        req_path = os.path.join(base, "requirements.txt")
        pyproject_path = os.path.join(base, "pyproject.toml")

        if os.path.isfile(req_path):
            try:
                with open(req_path, "r", encoding="utf-8") as f:
                    reqs = [
                        line.strip().split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0]
                        for line in f
                        if line.strip() and not line.strip().startswith("#")
                    ]
                snapshot["dependencies"]["requirements"] = reqs
            except Exception:
                pass

        if os.path.isfile(pyproject_path):
            try:
                with open(pyproject_path, "r", encoding="utf-8") as f:
                    pyproject_content = f.read()

                # Extract dependencies section
                deps_match = re.search(
                    r"dependencies\s*=\s*\[(.*?)\]",
                    pyproject_content,
                    re.DOTALL,
                )
                if deps_match:
                    deps = re.findall(r"['\"]([^'\"]+)['\"]", deps_match.group(1))
                    snapshot["dependencies"]["pyproject"] = deps

                # Python version
                python_match = re.search(r"requires-python\s*=\s*['\"]([^'\"]+)['\"]", pyproject_content)
                if python_match:
                    snapshot["python_version"] = python_match.group(1)
            except Exception:
                pass

        # .python-version file
        pv_path = os.path.join(base, ".python-version")
        if os.path.isfile(pv_path) and not snapshot.get("python_version"):
            try:
                with open(pv_path, "r") as f:
                    snapshot["python_version"] = f.read().strip()
            except Exception:
                pass

        return {
            "status": "success",
            "snapshot": snapshot,
        }

    # ── 11. get_dependency_injection ──────────────────────────────────

    def get_dependency_injection(self, router_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Depends() chains and build a dependency graph.

        Args:
            router_path: Optional router file path to filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        dependency_graph: Dict[str, Any] = {}
        dependency_providers: Dict[str, Any] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            if router_path and router_path not in rel_path:
                continue

            # Find all Depends() usages in function signatures
            func_pattern = re.compile(
                r"(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)",
                re.DOTALL,
            )
            for func_match in func_pattern.finditer(content):
                func_name = func_match.group(1)
                params_str = func_match.group(2)

                depends_matches = re.findall(
                    r"(\w+)\s*(?::\s*[\w\[\], ]+)?\s*=\s*Depends\s*\(\s*(\w+)\s*\)",
                    params_str,
                )

                if depends_matches:
                    deps = [
                        {"param": param, "dependency": dep}
                        for param, dep in depends_matches
                    ]
                    dependency_graph[func_name] = {
                        "file": rel_path,
                        "dependencies": deps,
                    }

            # Find dependency provider functions
            # def get_db(): yield db
            # def get_current_user(token: str = Depends(oauth2_scheme)):
            provider_pattern = re.compile(
                r"(?:async\s+)?def\s+(get_\w+|verify_\w+|require_\w+|check_\w+)\s*\(([^)]*)\)",
                re.DOTALL,
            )
            for pm in provider_pattern.finditer(content):
                provider_name = pm.group(1)
                params_str = pm.group(2)

                # Check what this provider itself depends on
                sub_deps = re.findall(
                    r"Depends\s*\(\s*(\w+)\s*\)",
                    params_str,
                )

                func_start = content.find(":", pm.end())
                if func_start != -1:
                    func_body = content[func_start:func_start + 500]
                    is_generator = "yield" in func_body
                else:
                    is_generator = False

                dependency_providers[provider_name] = {
                    "file": rel_path,
                    "sub_dependencies": sub_deps,
                    "is_generator": is_generator,
                }

        # Build resolved dependency chains
        chains: List[Dict[str, Any]] = []
        for func_name, data in dependency_graph.items():
            for dep_info in data["dependencies"]:
                chain = [dep_info["dependency"]]
                self._resolve_dep_chain(dep_info["dependency"], dependency_providers, chain, visited=set())
                chains.append({
                    "function": func_name,
                    "param": dep_info["param"],
                    "chain": chain,
                })

        return {
            "status": "success",
            "dependency_graph": dependency_graph,
            "providers": dependency_providers,
            "chains": chains[:30],
            "total_functions_with_deps": len(dependency_graph),
            "total_providers": len(dependency_providers),
        }

    def _resolve_dep_chain(self, dep_name: str, providers: Dict, chain: List[str],
                            visited: Set[str]) -> None:
        """Recursively resolve dependency chain."""
        if dep_name in visited:
            chain.append(f"(circular: {dep_name})")
            return
        visited.add(dep_name)

        provider = providers.get(dep_name)
        if provider and provider.get("sub_dependencies"):
            for sub_dep in provider["sub_dependencies"]:
                chain.append(sub_dep)
                self._resolve_dep_chain(sub_dep, providers, chain, visited)

    # ── 12. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware checks before editing a FastAPI symbol.

        Args:
            file_path: Relative file path (e.g., "app/routers/users.py")
            symbol_name: Symbol to check (e.g., "create_user", "UserSchema")
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
            "references": [],
            "impact": [],
            "suggestions": [],
            "anti_patterns": [],
        }

        # Find references
        all_files = self._scan_all_source(base)
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
        if "router" in file_path.lower() or "route" in file_path.lower():
            checks["impact"].append({
                "type": "route_change",
                "message": f"Changing '{symbol_name}' in a router affects API endpoints",
                "severity": "high",
            })

        if "model" in file_path.lower():
            checks["impact"].append({
                "type": "model_change",
                "message": f"Changing model '{symbol_name}' may require Alembic migration",
                "severity": "high",
            })

        if "schema" in file_path.lower():
            checks["impact"].append({
                "type": "schema_change",
                "message": f"Changing schema '{symbol_name}' affects API request/response contracts",
                "severity": "high",
            })

        if "service" in file_path.lower():
            checks["impact"].append({
                "type": "service_change",
                "message": f"Changing service '{symbol_name}' may affect dependent routers",
                "severity": "medium",
            })

        if "middleware" in file_path.lower():
            checks["impact"].append({
                "type": "middleware_change",
                "message": f"Changing middleware '{symbol_name}' affects all requests",
                "severity": "high",
            })

        # Check if symbol is a Pydantic model used as response_model
        for finfo in all_files:
            if f"response_model={symbol_name}" in finfo["content"] or f"response_model=List[{symbol_name}]" in finfo["content"]:
                checks["impact"].append({
                    "type": "response_model_usage",
                    "message": f"'{symbol_name}' is used as response_model in {finfo['rel_path']}",
                    "severity": "high",
                })
                break

        # Check if symbol is a dependency provider
        dep_info = self.get_dependency_injection()
        if dep_info.get("status") == "success":
            providers = dep_info.get("providers", {})
            if symbol_name in providers:
                users = [
                    func for func, data in dep_info.get("dependency_graph", {}).items()
                    if any(d["dependency"] == symbol_name for d in data.get("dependencies", []))
                ]
                if users:
                    checks["impact"].append({
                        "type": "dependency_provider",
                        "message": f"'{symbol_name}' is a dependency provider used by: {', '.join(users[:5])}",
                        "severity": "high",
                    })

        # Anti-pattern check
        for ap in self.ANTI_PATTERNS:
            if re.search(ap["pattern"], content):
                checks["anti_patterns"].append({
                    "pattern": ap["pattern"],
                    "severity": ap["severity"],
                    "message": ap["message"],
                })

        # Test file check
        test_candidates = [
            file_path.replace("app/", "tests/test_"),
            file_path.replace("app/", "tests/"),
            "tests/test_" + os.path.basename(file_path),
        ]
        test_found = False
        for tc in test_candidates:
            if os.path.isfile(os.path.join(base, tc)):
                checks["suggestions"].append(f"Tests exist at {tc} -- update after changes")
                test_found = True
                break
        if not test_found:
            checks["suggestions"].append("No test file found -- consider adding tests")

        return {
            "status": "success",
            "checks": checks,
            "total_references": sum(r.get("total", 0) for r in checks["references"]),
        }

    # ── verify_schema ────────────────────────────────────────────────

    def verify_schema(self, model_name: str, fields: list) -> Dict[str, Any]:
        """Verify that a model's fields are consistent across SQLAlchemy models,
        Pydantic schemas, and Alembic migrations."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "model": model_name,
            "expected_fields": fields,
            "sqlalchemy_fields": [],
            "pydantic_fields": [],
            "alembic_fields": [],
            "mismatches": [],
        }

        # Scan SQLAlchemy Column definitions
        sa_pattern = re.compile(
            rf"class\s+{re.escape(model_name)}\b[^:]*:.*?(?=\nclass\s|\Z)",
            re.DOTALL,
        )
        col_pattern = re.compile(r"(\w+)\s*=\s*(?:Column|mapped_column)\s*\(")

        for finfo in self._scan_files(base, "app/models"):
            match = sa_pattern.search(finfo["content"])
            if match:
                body = match.group(0)
                for col_m in col_pattern.finditer(body):
                    col_name = col_m.group(1)
                    if not col_name.startswith("__"):
                        result["sqlalchemy_fields"].append(col_name)

        # Scan Pydantic BaseModel fields
        pydantic_pattern = re.compile(
            rf"class\s+{re.escape(model_name)}(?:Base|Create|Update|Read|Schema|Response|Request)?\s*\(\s*(?:BaseModel|BaseSchema)[^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
            re.DOTALL,
        )
        field_pattern = re.compile(r"^\s+(\w+)\s*:\s*", re.MULTILINE)

        for subdir in ["app/schemas", "app/models", "app"]:
            for finfo in self._scan_files(base, subdir):
                for pm in pydantic_pattern.finditer(finfo["content"]):
                    body = pm.group(1)
                    for fm in field_pattern.finditer(body):
                        fname = fm.group(1)
                        if not fname.startswith("_") and fname not in ("model_config", "Config"):
                            result["pydantic_fields"].append(fname)

        # Scan Alembic migration columns
        alembic_dir = os.path.join(base, "alembic", "versions")
        if not os.path.isdir(alembic_dir):
            alembic_dir = os.path.join(base, "migrations", "versions")

        if os.path.isdir(alembic_dir):
            table_name = model_name.lower() + "s"
            add_col_pattern = re.compile(
                rf"(?:add_column|create_table)\s*\(\s*['\"](?:{re.escape(table_name)}|{re.escape(model_name.lower())})['\"]"
                r".*?sa\.Column\s*\(\s*['\"](\w+)['\"]",
                re.DOTALL,
            )
            for root, _, fnames in os.walk(alembic_dir):
                for fn in fnames:
                    if fn.endswith(".py"):
                        fpath = os.path.join(root, fn)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                            for am in add_col_pattern.finditer(content):
                                result["alembic_fields"].append(am.group(1))
                        except Exception:
                            continue

        # Check mismatches
        sa_set = set(result["sqlalchemy_fields"])
        pydantic_set = set(result["pydantic_fields"])
        expected_set = set(fields)

        for field in fields:
            if sa_set and field not in sa_set:
                result["mismatches"].append({
                    "field": field,
                    "issue": "missing_in_sqlalchemy",
                    "message": f"Field '{field}' not found in SQLAlchemy model",
                })
            if pydantic_set and field not in pydantic_set:
                result["mismatches"].append({
                    "field": field,
                    "issue": "missing_in_pydantic",
                    "message": f"Field '{field}' not found in Pydantic schema",
                })

        for sa_field in sa_set - expected_set:
            result["mismatches"].append({
                "field": sa_field,
                "issue": "extra_in_sqlalchemy",
                "message": f"Field '{sa_field}' in SQLAlchemy but not in expected fields",
            })

        result["status"] = "ok" if not result["mismatches"] else "mismatches_found"
        return result

    # ── detect_transaction_risks ─────────────────────────────────────

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing transaction management around multiple DB operations."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        risks: List[Dict[str, Any]] = []
        lines = content.split("\n")

        # Find function boundaries
        func_pattern = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
        functions = list(func_pattern.finditer(content))

        for idx, func_match in enumerate(functions):
            func_name = func_match.group(2)
            start = func_match.start()
            end = functions[idx + 1].start() if idx + 1 < len(functions) else len(content)
            func_body = content[start:end]

            # Count DB write operations
            db_writes = len(re.findall(
                r"(?:session|db)\s*\.\s*(?:add|delete|execute|commit|flush|merge|bulk_save_objects|bulk_insert_mappings)\s*\(",
                func_body,
            ))

            if db_writes >= 2:
                has_transaction = bool(re.search(
                    r"(?:async\s+)?with\s+.*(?:session\.begin|begin_nested|transaction)\s*\(",
                    func_body,
                ))
                if not has_transaction:
                    line_num = content[:start].count("\n") + 1
                    risks.append({
                        "function": func_name,
                        "line": line_num,
                        "db_operations": db_writes,
                        "risk": "multiple_db_writes_without_transaction",
                        "message": f"Function '{func_name}' has {db_writes} DB write operations without explicit transaction management",
                        "suggestion": "Wrap in 'async with session.begin():' or use session.begin_nested()",
                    })

            # Check for cache operations inside transaction
            if re.search(r"(?:async\s+)?with\s+.*(?:session\.begin|transaction)", func_body):
                cache_in_txn = re.findall(
                    r"(?:cache|redis)\s*\.\s*(?:set|delete|invalidate|put)\s*\(",
                    func_body,
                )
                if cache_in_txn:
                    line_num = content[:start].count("\n") + 1
                    risks.append({
                        "function": func_name,
                        "line": line_num,
                        "risk": "cache_in_transaction",
                        "message": f"Cache operation inside transaction in '{func_name}' -- cache may become inconsistent on rollback",
                        "suggestion": "Move cache operations after transaction commit",
                    })

        return {"status": "ok", "file": file_path, "risks": risks, "total_risks": len(risks)}

    # ── get_domain_rules ─────────────────────────────────────────────

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check domain-specific rules for FastAPI file types."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        violations: List[Dict[str, Any]] = []
        file_type = "unknown"
        rel_path = os.path.relpath(full_path, base) if base else file_path

        # Determine file type and apply rules
        if re.search(r"(?:routers?|api|endpoints?|routes?)", rel_path, re.IGNORECASE):
            file_type = "router"
            # Rule: routers require Depends(auth)
            route_decorators = re.findall(
                r"@(?:router|app)\s*\.\s*(?:get|post|put|delete|patch)\s*\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)",
                content,
            )
            for func_name, params in route_decorators:
                if "Depends" not in params and func_name not in ("health", "healthcheck", "health_check", "root"):
                    violations.append({
                        "rule": "router_requires_auth",
                        "severity": "warning",
                        "function": func_name,
                        "message": f"Route handler '{func_name}' has no Depends() injection -- consider adding authentication",
                    })

        elif re.search(r"schemas?", rel_path, re.IGNORECASE):
            file_type = "schema"
            # Rule: Pydantic schemas should have Field validators
            class_matches = re.finditer(
                r"class\s+(\w+)\s*\([^)]*BaseModel[^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
                content, re.DOTALL,
            )
            for cm in class_matches:
                cls_name = cm.group(1)
                body = cm.group(2)
                field_count = len(re.findall(r"^\s+\w+\s*:", body, re.MULTILINE))
                validator_count = len(re.findall(r"@(?:field_validator|validator|model_validator)", body))
                if field_count > 3 and validator_count == 0:
                    violations.append({
                        "rule": "schema_requires_validators",
                        "severity": "info",
                        "class": cls_name,
                        "message": f"Schema '{cls_name}' has {field_count} fields but no validators",
                    })

        elif re.search(r"(?:crud|repository|dal|dao)", rel_path, re.IGNORECASE):
            file_type = "crud"
            # Rule: CRUD functions require session management
            func_pattern = re.compile(r"(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
            for fm in func_pattern.finditer(content):
                func_name = fm.group(1)
                params = fm.group(2)
                if func_name.startswith("_"):
                    continue
                if not re.search(r"(?:session|db|Session|AsyncSession)", params):
                    violations.append({
                        "rule": "crud_requires_session",
                        "severity": "warning",
                        "function": func_name,
                        "message": f"CRUD function '{func_name}' does not accept a session parameter",
                    })

        elif re.search(r"(?:tasks?|background|workers?|jobs?|celery)", rel_path, re.IGNORECASE):
            file_type = "background_task"
            # Rule: background tasks require error handling
            func_matches = re.finditer(
                r"(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->.*?)?:(.*?)(?=\n(?:async\s+)?def\s|\Z)",
                content, re.DOTALL,
            )
            for fm in func_matches:
                func_name = fm.group(1)
                body = fm.group(2)
                if func_name.startswith("_"):
                    continue
                has_try = "try:" in body
                has_except = "except" in body
                if not (has_try and has_except):
                    violations.append({
                        "rule": "background_task_requires_error_handling",
                        "severity": "error",
                        "function": func_name,
                        "message": f"Background task '{func_name}' lacks try/except error handling",
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
        """Generate a pytest test skeleton for a FastAPI endpoint or function."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result: Dict[str, Any] = {"file": file_path, "symbol": symbol, "skeleton": "", "test_type": "unknown"}
        rel_path = os.path.relpath(full_path, base) if base else file_path
        module_path = rel_path.replace(os.sep, ".").replace(".py", "")

        # Find the symbol definition
        func_match = re.search(
            rf"(async\s+)?def\s+{re.escape(symbol)}\s*\(([^)]*)\)(?:\s*->\s*([^\s:]+))?\s*:",
            content,
        )

        is_async = bool(func_match and func_match.group(1))
        params = func_match.group(2).strip() if func_match else ""
        return_type = func_match.group(3) if func_match and func_match.group(3) else "None"

        # Detect if it's a route handler
        is_route = bool(re.search(
            rf"@(?:router|app)\s*\.\s*(?:get|post|put|delete|patch)\s*\([^)]*\)\s*\n.*?def\s+{re.escape(symbol)}\s*\(",
            content, re.DOTALL,
        ))

        # Extract HTTP method and path
        route_method = "get"
        route_path = f"/{symbol}"
        if is_route:
            route_match = re.search(
                rf"@(?:router|app)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
                content[:content.index(f"def {symbol}")],
            )
            if route_match:
                route_method = route_match.group(1)
                route_path = route_match.group(2)

        # Extract dependencies from params
        deps = re.findall(r"(\w+)\s*:\s*\w+\s*=\s*Depends\s*\(\s*(\w+)\s*\)", params)

        if is_route and is_async:
            result["test_type"] = "async_route"
            dep_mocks = "\n".join(
                f"    # Mock dependency: {d[1]}\n    app.dependency_overrides[{d[1]}] = lambda: mock_{d[0]}"
                for d in deps
            )
            result["skeleton"] = (
                f"import pytest\n"
                f"from httpx import AsyncClient, ASGITransport\n"
                f"from {module_path.split('.')[0]}.main import app\n\n\n"
                f"@pytest.mark.anyio\n"
                f"async def test_{symbol}():\n"
                f"    \"\"\"Test {symbol} endpoint.\"\"\"\n"
                f"{dep_mocks}\n"
                f"    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:\n"
                f"        response = await client.{route_method}('{route_path}')\n"
                f"        assert response.status_code == 200\n"
                f"        data = response.json()\n"
                f"        # TODO: Add specific assertions\n"
            )
        elif is_route:
            result["test_type"] = "sync_route"
            dep_mocks = "\n".join(
                f"    # Mock dependency: {d[1]}\n    app.dependency_overrides[{d[1]}] = lambda: mock_{d[0]}"
                for d in deps
            )
            result["skeleton"] = (
                f"import pytest\n"
                f"from fastapi.testclient import TestClient\n"
                f"from {module_path.split('.')[0]}.main import app\n\n\n"
                f"@pytest.fixture\n"
                f"def client():\n"
                f"    return TestClient(app)\n\n\n"
                f"def test_{symbol}(client):\n"
                f"    \"\"\"Test {symbol} endpoint.\"\"\"\n"
                f"{dep_mocks}\n"
                f"    response = client.{route_method}('{route_path}')\n"
                f"    assert response.status_code == 200\n"
                f"    data = response.json()\n"
                f"    # TODO: Add specific assertions\n"
            )
        else:
            result["test_type"] = "async_unit" if is_async else "unit"
            prefix = "@pytest.mark.anyio\nasync " if is_async else ""
            await_prefix = "await " if is_async else ""
            result["skeleton"] = (
                f"import pytest\n"
                f"from {module_path} import {symbol}\n\n\n"
                f"{prefix}def test_{symbol}():\n"
                f"    \"\"\"Test {symbol} function.\"\"\"\n"
                f"    # TODO: Set up test fixtures\n"
                f"    result = {await_prefix}{symbol}()\n"
                f"    # TODO: Add assertions\n"
                f"    assert result is not None\n"
            )

        result["status"] = "ok"
        return result

    # ── match_view_guards ────────────────────────────────────────────

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match backend Depends(auth) checks in routers to frontend conditional renders."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
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
            "backend_guards": [],
            "frontend_guards": [],
            "unmatched": [],
        }

        # Find auth dependencies on the symbol (route handler)
        func_match = re.search(
            rf"@(?:router|app)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"].*?\)\s*\n"
            rf".*?def\s+{re.escape(symbol)}\s*\(([^)]*)\)",
            content, re.DOTALL,
        )

        if not func_match:
            return {"status": "ok", "file": file_path, "symbol": symbol,
                    "message": f"Symbol '{symbol}' is not a route handler", **result}

        route_path = func_match.group(2)
        params = func_match.group(3)

        # Extract auth-related dependencies
        auth_deps = re.findall(
            r"Depends\s*\(\s*(\w*(?:auth|current_user|permission|role|token|security)\w*)\s*\)",
            params, re.IGNORECASE,
        )
        for dep in auth_deps:
            result["backend_guards"].append({
                "type": "depends",
                "dependency": dep,
                "route": route_path,
            })

        # Also check for security decorators
        decorator_guards = re.findall(
            rf"@(?:requires|has_permission|role_required|auth_required)\s*\(([^)]*)\)\s*\n.*?def\s+{re.escape(symbol)}",
            content, re.DOTALL,
        )
        for dg in decorator_guards:
            result["backend_guards"].append({
                "type": "decorator",
                "permission": dg.strip().strip("'\""),
                "route": route_path,
            })

        # Scan frontend files for matching conditional renders
        frontend_dirs = ["frontend/src", "frontend", "static", "client/src", "web/src", "app/templates"]
        frontend_exts = (".vue", ".tsx", ".jsx", ".svelte", ".html", ".js", ".ts")

        for fdir in frontend_dirs:
            scan_dir = os.path.join(base, fdir)
            if not os.path.isdir(scan_dir):
                continue
            for root, dirs, files in os.walk(scan_dir):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "dist", "build")]
                for fname in files:
                    if not fname.endswith(frontend_exts):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            fe_content = f.read()
                    except Exception:
                        continue

                    rel = os.path.relpath(fpath, base)

                    # Check if this frontend file references the route
                    if not re.search(re.escape(route_path), fe_content):
                        continue

                    # Look for conditional renders related to auth
                    auth_conditions = re.findall(
                        r"(?:v-if|v-show|\{.*?&&|\? )\s*['\"]?[\w.]*(?:auth|isAuth|logged|user|role|permission|token)[\w.]*",
                        fe_content, re.IGNORECASE,
                    )
                    for cond in auth_conditions:
                        result["frontend_guards"].append({
                            "file": rel,
                            "condition": cond.strip()[:80],
                            "route_reference": route_path,
                        })

        # Identify unmatched guards
        if result["backend_guards"] and not result["frontend_guards"]:
            result["unmatched"].append({
                "severity": "warning",
                "message": f"Route '{route_path}' has backend auth guards but no matching frontend conditional rendering found",
            })
        if result["frontend_guards"] and not result["backend_guards"]:
            result["unmatched"].append({
                "severity": "error",
                "message": f"Route '{route_path}' has frontend auth checks but no backend Depends() guard -- potential security issue",
            })

        result["status"] = "ok"
        return result
