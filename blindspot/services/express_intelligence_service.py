"""
Express.js Intelligence Service - Express/Node.js-specific code analysis.

Provides route mapping, Mongoose/Sequelize/TypeORM model parsing, middleware
chain resolution, template dependency analysis, and flow tracing for
Express.js applications.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

from .base_service import BaseService

logger = logging.getLogger(__name__)


class ExpressIntelligenceService(BaseService):
    """Express.js-specific code intelligence for Node.js projects."""

    SCAN_DIRS = {
        "models": "src/models",
        "controllers": "src/controllers",
        "routes": "src/routes",
        "middleware": "src/middleware",
        "services": "src/services",
        "views": "src/views",
        "tests": "tests",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bconsole\.log\s*\(",
            "severity": "warning",
            "message": "console.log in production code -- use a proper logger",
            "file_types": ["js", "ts"],
        },
        {
            "pattern": r"\bvar\s+",
            "severity": "warning",
            "message": "Use const/let instead of var",
            "file_types": ["js", "ts"],
        },
        {
            "pattern": r"function\s*\([^)]*\)\s*\{[^}]*function\s*\([^)]*\)\s*\{[^}]*function\s*\(",
            "severity": "error",
            "message": "Callback hell -- nested callbacks > 3 levels deep",
            "file_types": ["js"],
        },
        {
            "pattern": r"\brequire\s*\([^)]+\)",
            "severity": "info",
            "message": "require() inside function body -- prefer top-level imports",
            "file_types": ["js"],
        },
    ]

    PATTERN_REGISTRY = {
        "middleware": {
            "patterns": [
                r"(?:app|router)\.use\s*\(",
                r"module\.exports\s*=\s*(?:async\s+)?(?:function|\()",
            ],
            "description": "Express middleware patterns",
        },
        "auth": {
            "patterns": [
                r"passport\.",
                r"jwt\.",
                r"jsonwebtoken",
                r"bcrypt",
                r"authenticate",
                r"isAuthenticated",
                r"req\.user",
                r"Bearer\s+",
            ],
            "description": "Authentication/authorization patterns",
        },
        "upload": {
            "patterns": [
                r"multer",
                r"upload\.\w+\(",
                r"formidable",
                r"busboy",
                r"multipart",
            ],
            "description": "File upload patterns",
        },
        "pagination": {
            "patterns": [
                r"\.skip\s*\(",
                r"\.limit\s*\(",
                r"page\s*[=:]",
                r"offset\s*[=:]",
                r"paginate",
            ],
            "description": "Pagination patterns",
        },
        "rate-limit": {
            "patterns": [
                r"rateLimit",
                r"rate-limit",
                r"express-rate-limit",
                r"throttle",
            ],
            "description": "Rate limiting patterns",
        },
        "cors": {
            "patterns": [
                r"\bcors\b",
                r"Access-Control",
                r"allowedOrigins",
            ],
            "description": "CORS configuration patterns",
        },
        "socket": {
            "patterns": [
                r"socket\.io",
                r"io\.on\(",
                r"socket\.on\(",
                r"socket\.emit\(",
                r"ws\b",
                r"WebSocket",
            ],
            "description": "WebSocket/Socket.IO patterns",
        },
        "validation": {
            "patterns": [
                r"express-validator",
                r"Joi\.",
                r"\.validate\(",
                r"z\.object\(",
                r"z\.string\(",
                r"validationResult",
                r"check\s*\(",
                r"body\s*\(",
            ],
            "description": "Input validation patterns",
        },
        "error-handler": {
            "patterns": [
                r"err\s*,\s*req\s*,\s*res\s*,\s*next",
                r"\.status\s*\(\s*[45]\d{2}\s*\)",
                r"createError",
                r"HttpError",
                r"AppError",
            ],
            "description": "Error handling patterns",
        },
    }

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    def _scan_files(self, base: str, directory: str, extensions: tuple = (".js", ".ts", ".mjs")) -> List[dict]:
        """Scan directory recursively for files with given extensions.

        Returns list of dicts with 'path' (absolute), 'rel_path' (relative), and 'content'.
        """
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, _, files in os.walk(full_dir):
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

    def _scan_all_source(self, base: str, extensions: tuple = (".js", ".ts", ".mjs")) -> List[dict]:
        """Scan all known source directories."""
        all_files = []
        seen = set()
        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory, extensions):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)
        # Also scan root-level files like app.js, server.js, index.js
        for fname in os.listdir(base):
            fpath = os.path.join(base, fname)
            if os.path.isfile(fpath) and fname.endswith(extensions) and fpath not in seen:
                seen.add(fpath)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    all_files.append({"path": fpath, "rel_path": fname, "content": content})
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)
        return all_files

    # ── 1. get_model_schema ───────────────────────────────────────────

    def get_model_schema(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Mongoose schemas, Sequelize define(), and TypeORM @Entity/@Column decorators.

        Extracts fields, types, relations, indexes, and validators from
        model definition files.

        Args:
            model_name: Optional model name to filter (e.g., "User"). If omitted, returns all.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        models_dir = os.path.join(base, self.SCAN_DIRS["models"])
        if not os.path.isdir(models_dir):
            return {"status": "error", "message": f"Models directory not found at {self.SCAN_DIRS['models']}"}

        all_models: Dict[str, Any] = {}
        model_files = self._scan_files(base, self.SCAN_DIRS["models"])

        for finfo in model_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # --- Mongoose schema detection ---
            mongoose_schemas = self._parse_mongoose_schemas(content, rel_path)
            for name, schema_info in mongoose_schemas.items():
                if model_name and name.lower() != model_name.lower():
                    continue
                all_models[name] = schema_info

            # --- Sequelize model detection ---
            sequelize_models = self._parse_sequelize_models(content, rel_path)
            for name, model_info in sequelize_models.items():
                if model_name and name.lower() != model_name.lower():
                    continue
                all_models[name] = model_info

            # --- TypeORM entity detection ---
            typeorm_models = self._parse_typeorm_entities(content, rel_path)
            for name, entity_info in typeorm_models.items():
                if model_name and name.lower() != model_name.lower():
                    continue
                all_models[name] = entity_info

        if model_name and not all_models:
            return {"status": "error", "message": f"Model '{model_name}' not found"}

        return {
            "status": "success",
            "models": all_models,
            "total_models": len(all_models),
        }

    def _parse_mongoose_schemas(self, content: str, rel_path: str) -> Dict[str, Any]:
        """Parse Mongoose schema definitions."""
        models: Dict[str, Any] = {}

        # Pattern: new Schema({ ... }) or new mongoose.Schema({ ... })
        schema_pattern = re.compile(
            r"(?:const|let|var)\s+(\w+)\s*=\s*new\s+(?:mongoose\.)?Schema\s*\(\s*\{",
            re.MULTILINE,
        )

        for match in schema_pattern.finditer(content):
            schema_var = match.group(1)
            start = match.end() - 1  # start at the opening {
            fields = self._extract_brace_block(content, start)

            parsed_fields = self._parse_mongoose_fields(fields)

            # Find model name: mongoose.model('ModelName', schemaVar)
            model_name_match = re.search(
                rf"""(?:mongoose\.model|model)\s*\(\s*['"]([\w]+)['"]\s*,\s*{re.escape(schema_var)}""",
                content,
            )
            name = model_name_match.group(1) if model_name_match else schema_var.replace("Schema", "").replace("schema", "")
            if not name:
                name = schema_var

            # Extract indexes
            indexes = []
            index_pattern = re.compile(
                rf"{re.escape(schema_var)}\.index\s*\(\s*\{{([^}}]*)\}}",
            )
            for idx_match in index_pattern.finditer(content):
                indexes.append(idx_match.group(1).strip())

            # Extract validators
            validators = re.findall(
                r"validate\s*:\s*(?:\{[^}]*\}|\[[^\]]*\]|[\w.]+)",
                fields,
            )

            # Extract virtuals
            virtuals = re.findall(
                rf"{re.escape(schema_var)}\.virtual\s*\(\s*['\"](\w+)['\"]",
                content,
            )

            # Extract middleware/hooks
            hooks = []
            hook_pattern = re.compile(
                rf"{re.escape(schema_var)}\.(?:pre|post)\s*\(\s*['\"](\w+)['\"]",
            )
            for hk in hook_pattern.finditer(content):
                hooks.append(hk.group(1))

            models[name] = {
                "file": rel_path,
                "orm": "mongoose",
                "fields": parsed_fields,
                "indexes": indexes,
                "validators": validators,
                "virtuals": virtuals,
                "hooks": hooks,
            }

        return models

    def _parse_mongoose_fields(self, block: str) -> List[Dict[str, str]]:
        """Parse field definitions from a Mongoose schema block."""
        fields = []
        # Match: fieldName: { type: Type, ... } or fieldName: Type
        field_pattern = re.compile(
            r"(\w+)\s*:\s*(?:\{\s*type\s*:\s*([\w.]+)|(\w+(?:\.\w+)?))",
        )
        for m in field_pattern.finditer(block):
            field_name = m.group(1)
            field_type = m.group(2) or m.group(3) or "Mixed"
            required = "required" in block[m.start():m.start() + 200] if m.start() + 200 <= len(block) else False

            # Check for ref (relation)
            ref_match = re.search(
                rf"{re.escape(field_name)}\s*:.*?ref\s*:\s*['\"](\w+)['\"]",
                block,
                re.DOTALL,
            )
            relation = ref_match.group(1) if ref_match else None

            field_info: Dict[str, Any] = {
                "name": field_name,
                "type": field_type,
            }
            if required:
                field_info["required"] = True
            if relation:
                field_info["relation"] = relation
            fields.append(field_info)

        return fields

    def _parse_sequelize_models(self, content: str, rel_path: str) -> Dict[str, Any]:
        """Parse Sequelize model definitions."""
        models: Dict[str, Any] = {}

        # Pattern: sequelize.define('ModelName', { ... })
        define_pattern = re.compile(
            r"(?:sequelize|connection|db)\.define\s*\(\s*['\"](\w+)['\"]\s*,\s*\{",
            re.MULTILINE,
        )

        # Also: class Model extends Model { ... static init(sequelize) { ... } }
        class_pattern = re.compile(
            r"class\s+(\w+)\s+extends\s+(?:Sequelize\.)?Model\s*\{",
            re.MULTILINE,
        )

        for match in define_pattern.finditer(content):
            name = match.group(1)
            start = match.end() - 1
            fields_block = self._extract_brace_block(content, start)
            parsed_fields = self._parse_sequelize_fields(fields_block)

            # Extract associations
            associations = self._parse_sequelize_associations(content, name)

            models[name] = {
                "file": rel_path,
                "orm": "sequelize",
                "fields": parsed_fields,
                "associations": associations,
            }

        for match in class_pattern.finditer(content):
            name = match.group(1)
            if name in models:
                continue

            class_block = self._extract_brace_block(content, match.end() - 1)

            # Look for init() call with field definitions
            init_match = re.search(r"(?:super\.init|this\.init|" + re.escape(name) + r"\.init)\s*\(\s*\{", class_block)
            parsed_fields = []
            if init_match:
                fields_block = self._extract_brace_block(class_block, init_match.end() - 1)
                parsed_fields = self._parse_sequelize_fields(fields_block)

            associations = self._parse_sequelize_associations(content, name)

            models[name] = {
                "file": rel_path,
                "orm": "sequelize",
                "fields": parsed_fields,
                "associations": associations,
            }

        return models

    def _parse_sequelize_fields(self, block: str) -> List[Dict[str, str]]:
        """Parse Sequelize field definitions."""
        fields = []
        # fieldName: { type: DataTypes.STRING, allowNull: false, ... }
        field_pattern = re.compile(
            r"(\w+)\s*:\s*\{[^}]*type\s*:\s*(?:DataTypes\.|Sequelize\.)?(\w+)",
        )
        for m in field_pattern.finditer(block):
            field_info: Dict[str, Any] = {
                "name": m.group(1),
                "type": m.group(2),
            }
            # Check for allowNull
            region = block[m.start():m.start() + 300]
            if "allowNull: false" in region or "allowNull:false" in region:
                field_info["required"] = True
            if "unique: true" in region or "unique:true" in region:
                field_info["unique"] = True
            if "primaryKey: true" in region or "primaryKey:true" in region:
                field_info["primary_key"] = True
            # Check for references (foreign key)
            ref_match = re.search(r"references\s*:\s*\{\s*model\s*:\s*['\"](\w+)['\"]", region)
            if ref_match:
                field_info["references"] = ref_match.group(1)
            fields.append(field_info)

        return fields

    def _parse_sequelize_associations(self, content: str, model_name: str) -> List[Dict[str, str]]:
        """Parse Sequelize association definitions."""
        associations = []
        assoc_pattern = re.compile(
            rf"{re.escape(model_name)}\.(?:hasMany|hasOne|belongsTo|belongsToMany)\s*\(\s*(?:models\.)?(\w+)",
        )
        for m in assoc_pattern.finditer(content):
            assoc_type_match = re.search(r"\.(hasMany|hasOne|belongsTo|belongsToMany)", m.group(0))
            associations.append({
                "type": assoc_type_match.group(1) if assoc_type_match else "unknown",
                "target": m.group(1),
            })
        return associations

    def _parse_typeorm_entities(self, content: str, rel_path: str) -> Dict[str, Any]:
        """Parse TypeORM entity definitions using decorators."""
        models: Dict[str, Any] = {}

        # Pattern: @Entity() class ModelName { ... }
        entity_pattern = re.compile(
            r"@Entity\s*\((?:[^)]*)\)?\s*(?:export\s+)?class\s+(\w+)",
            re.MULTILINE,
        )

        for match in entity_pattern.finditer(content):
            name = match.group(1)
            # Find the class body
            class_start = content.find("{", match.end())
            if class_start == -1:
                continue
            class_block = self._extract_brace_block(content, class_start)

            fields = []
            relations = []

            # Parse @Column decorators
            col_pattern = re.compile(
                r"@Column\s*\(\s*(?:\{[^}]*type\s*:\s*['\"]?(\w+)['\"]?[^}]*\}|['\"](\w+)['\"])?\s*\)\s*\n?\s*(\w+)\s*[!?]?\s*:\s*(\w+)",
            )
            for col_match in col_pattern.finditer(class_block):
                col_type = col_match.group(1) or col_match.group(2) or col_match.group(4)
                fields.append({
                    "name": col_match.group(3),
                    "type": col_type,
                    "decorator": "Column",
                })

            # Parse @PrimaryGeneratedColumn
            pk_pattern = re.compile(
                r"@PrimaryGeneratedColumn\s*\([^)]*\)\s*\n?\s*(\w+)\s*[!?]?\s*:\s*(\w+)",
            )
            for pk_match in pk_pattern.finditer(class_block):
                fields.append({
                    "name": pk_match.group(1),
                    "type": pk_match.group(2),
                    "decorator": "PrimaryGeneratedColumn",
                    "primary_key": True,
                })

            # Parse relation decorators
            rel_pattern = re.compile(
                r"@(OneToOne|OneToMany|ManyToOne|ManyToMany)\s*\(\s*\(\s*\)\s*=>\s*(\w+)",
            )
            for rel_match in rel_pattern.finditer(class_block):
                relations.append({
                    "type": rel_match.group(1),
                    "target": rel_match.group(2),
                })

            # Parse @JoinColumn
            join_cols = re.findall(
                r"@JoinColumn\s*\(\s*\{[^}]*name\s*:\s*['\"](\w+)['\"]",
                class_block,
            )

            models[name] = {
                "file": rel_path,
                "orm": "typeorm",
                "fields": fields,
                "relations": relations,
                "join_columns": join_cols,
            }

        return models

    def _extract_brace_block(self, content: str, start: int) -> str:
        """Extract content inside matching braces starting at 'start' position."""
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

    # ── 2. get_route_map ──────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Express router files and extract route definitions.

        Supports router.get/post/put/delete(), app.use(), route grouping,
        both inline and imported handlers.

        Args:
            filter_prefix: Optional URL prefix filter (e.g., "/api/users").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes: List[Dict[str, Any]] = []
        all_files = self._scan_all_source(base)

        # Route definition patterns
        route_pattern = re.compile(
            r"(?:app|router|route|server)\s*\."
            r"(get|post|put|patch|delete|all|options|head)\s*\(\s*"
            r"['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*(.+?))?\s*\)",
            re.MULTILINE,
        )

        # app.use() with path
        use_pattern = re.compile(
            r"(?:app|router)\s*\.use\s*\(\s*['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*(.+?))?\s*\)",
            re.MULTILINE,
        )

        # Router module mounting: app.use('/path', require('./routes/xyz'))
        mount_pattern = re.compile(
            r"(?:app|router)\s*\.use\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*"
            r"(?:require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)|(\w+))",
            re.MULTILINE,
        )

        # Express Router() detection for prefix resolution
        router_prefix_map: Dict[str, str] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Detect router mounting to resolve prefixes
            for mount_match in mount_pattern.finditer(content):
                prefix = mount_match.group(1)
                module_path = mount_match.group(2) or mount_match.group(3)
                if module_path:
                    router_prefix_map[module_path] = prefix

            # Determine prefix for this file based on mounting
            file_prefix = ""
            for module_key, prefix in router_prefix_map.items():
                if module_key in rel_path or os.path.basename(rel_path).replace(".js", "").replace(".ts", "") in module_key:
                    file_prefix = prefix
                    break

            # Parse route definitions
            for match in route_pattern.finditer(content):
                method = match.group(1).upper()
                path = match.group(2)
                handler_str = match.group(3) or ""

                full_path = file_prefix + path if not path.startswith(file_prefix) else path

                # Parse handler chain (middleware + final handler)
                handlers = self._parse_handler_chain(handler_str)

                route_info: Dict[str, Any] = {
                    "method": method,
                    "path": full_path,
                    "file": rel_path,
                    "handlers": handlers,
                }

                routes.append(route_info)

            # Parse app.use() mount points
            for use_match in use_pattern.finditer(content):
                path = use_match.group(1)
                handler_str = use_match.group(2) or ""
                if not any(r["path"] == path and r["method"] == "USE" for r in routes):
                    routes.append({
                        "method": "USE",
                        "path": path,
                        "file": rel_path,
                        "handlers": self._parse_handler_chain(handler_str),
                    })

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

    def _parse_handler_chain(self, handler_str: str) -> List[str]:
        """Parse a comma-separated handler/middleware chain string."""
        if not handler_str:
            return []
        handlers = []
        # Split on commas, but respect parentheses
        depth = 0
        current = ""
        for ch in handler_str:
            if ch in "([{":
                depth += 1
                current += ch
            elif ch in ")]}":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0:
                cleaned = current.strip()
                if cleaned:
                    handlers.append(cleaned)
                current = ""
            else:
                current += ch
        cleaned = current.strip()
        if cleaned:
            handlers.append(cleaned)
        return handlers

    # ── 3. get_migration_schema ───────────────────────────────────────

    def get_migration_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Sequelize migrations (createTable, addColumn) or Knex migrations
        (table.string/integer/etc).

        Args:
            table_name: Optional table name filter. If omitted, returns all tables.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        tables: Dict[str, Any] = {}

        # Look for migration directories
        migration_dirs = [
            "migrations",
            "db/migrations",
            "database/migrations",
            "src/migrations",
            "src/database/migrations",
        ]

        found_dir = None
        for mdir in migration_dirs:
            full = os.path.join(base, mdir)
            if os.path.isdir(full):
                found_dir = mdir
                break

        if not found_dir:
            return {"status": "error", "message": "No migrations directory found"}

        migration_files = self._scan_files(base, found_dir)

        for finfo in sorted(migration_files, key=lambda x: x["rel_path"]):
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # --- Sequelize createTable ---
            create_pattern = re.compile(
                r"queryInterface\.createTable\s*\(\s*['\"](\w+)['\"]\s*,\s*\{",
                re.MULTILINE,
            )
            for match in create_pattern.finditer(content):
                tname = match.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue

                block_start = match.end() - 1
                fields_block = self._extract_brace_block(content, block_start)
                columns = self._parse_sequelize_migration_columns(fields_block)

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "file": rel_path}
                tables[tname]["columns"].extend(columns)

            # --- Sequelize addColumn ---
            add_col_pattern = re.compile(
                r"queryInterface\.addColumn\s*\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*,\s*\{([^}]*)\}",
                re.MULTILINE,
            )
            for match in add_col_pattern.finditer(content):
                tname = match.group(1)
                col_name = match.group(2)
                col_def = match.group(3)
                if table_name and tname.lower() != table_name.lower():
                    continue
                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "file": rel_path}
                type_match = re.search(r"type\s*:\s*(?:Sequelize\.|DataTypes\.)?(\w+)", col_def)
                tables[tname]["columns"].append({
                    "name": col_name,
                    "type": type_match.group(1) if type_match else "unknown",
                    "operation": "addColumn",
                    "migration_file": rel_path,
                })

            # --- Sequelize addIndex ---
            add_idx_pattern = re.compile(
                r"queryInterface\.addIndex\s*\(\s*['\"](\w+)['\"]\s*,\s*\[([^\]]*)\]",
            )
            for match in add_idx_pattern.finditer(content):
                tname = match.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue
                index_cols = re.findall(r"['\"](\w+)['\"]", match.group(2))
                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "file": rel_path}
                tables[tname]["indexes"].append({"columns": index_cols, "migration_file": rel_path})

            # --- Knex migrations ---
            knex_create_pattern = re.compile(
                r"knex\.schema\.createTable\s*\(\s*['\"](\w+)['\"]\s*,\s*(?:function\s*\(\s*(\w+)\s*\)|\(\s*(\w+)\s*\)\s*=>)",
            )
            for match in knex_create_pattern.finditer(content):
                tname = match.group(1)
                table_var = match.group(2) or match.group(3) or "table"
                if table_name and tname.lower() != table_name.lower():
                    continue

                # Parse table.string('name'), table.integer('age'), etc.
                knex_col_pattern = re.compile(
                    rf"{re.escape(table_var)}\.(string|integer|bigInteger|text|boolean|date|datetime|timestamp|float|decimal|binary|json|jsonb|uuid|increments|bigIncrements|enum|specificType)\s*\(\s*['\"](\w+)['\"]",
                )
                columns = []
                for col_match in knex_col_pattern.finditer(content):
                    columns.append({
                        "name": col_match.group(2),
                        "type": col_match.group(1),
                    })

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "file": rel_path}
                tables[tname]["columns"].extend(columns)

        if table_name and not tables:
            return {"status": "error", "message": f"Table '{table_name}' not found in migrations"}

        return {
            "status": "success",
            "tables": tables,
            "total_tables": len(tables),
        }

    def _parse_sequelize_migration_columns(self, block: str) -> List[Dict[str, Any]]:
        """Parse column definitions from a Sequelize createTable block."""
        columns = []
        col_pattern = re.compile(
            r"(\w+)\s*:\s*\{[^}]*type\s*:\s*(?:Sequelize\.|DataTypes\.)?(\w+)",
        )
        for m in col_pattern.finditer(block):
            col_info: Dict[str, Any] = {
                "name": m.group(1),
                "type": m.group(2),
            }
            region = block[m.start():m.start() + 300]
            if "allowNull: false" in region:
                col_info["nullable"] = False
            if "primaryKey: true" in region:
                col_info["primary_key"] = True
            if "defaultValue:" in region:
                default_match = re.search(r"defaultValue\s*:\s*([^,}\n]+)", region)
                if default_match:
                    col_info["default"] = default_match.group(1).strip()
            ref_match = re.search(r"references\s*:\s*\{\s*model\s*:\s*['\"](\w+)['\"]", region)
            if ref_match:
                col_info["references"] = ref_match.group(1)
            columns.append(col_info)
        return columns

    # ── 4. get_template_dependencies ──────────────────────────────────

    def get_template_dependencies(self, template_path: str) -> Dict[str, Any]:
        """
        Parse EJS (<%- include() %>), Handlebars ({{> partial}}), and
        Pug (include/extends) templates. Return template dependency tree.

        Args:
            template_path: Relative path to the template file.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, template_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"Template file not found: {template_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read file: {e}"}

        dependencies: Dict[str, Any] = {
            "file": template_path,
            "includes": [],
            "partials": [],
            "extends": None,
            "blocks": [],
            "template_engine": "unknown",
        }

        # Detect engine
        if template_path.endswith(".ejs"):
            dependencies["template_engine"] = "ejs"
            # EJS includes: <%- include('partial') %> or <%- include('partial', data) %>
            ejs_include = re.findall(
                r"<%[-=]?\s*include\s*\(\s*['\"]([^'\"]+)['\"]",
                content,
            )
            dependencies["includes"] = ejs_include

        elif template_path.endswith((".hbs", ".handlebars")):
            dependencies["template_engine"] = "handlebars"
            # Handlebars partials: {{> partialName}} or {{> partialName context}}
            hbs_partials = re.findall(r"\{\{>\s*(\S+?)(?:\s+[^}]*)?\}\}", content)
            dependencies["partials"] = hbs_partials

            # Handlebars layout: {{!< layout}}
            layout_match = re.search(r"\{\{!<\s*(\S+)\s*\}\}", content)
            if layout_match:
                dependencies["extends"] = layout_match.group(1)

            # Block helpers
            block_helpers = re.findall(r"\{\{#(\w+)", content)
            dependencies["blocks"] = list(set(block_helpers))

        elif template_path.endswith(".pug"):
            dependencies["template_engine"] = "pug"
            # Pug extends: extends layout
            extends_match = re.search(r"^extends\s+(.+)$", content, re.MULTILINE)
            if extends_match:
                dependencies["extends"] = extends_match.group(1).strip()

            # Pug includes: include partials/header
            pug_includes = re.findall(r"^(?:\s*)include\s+(.+)$", content, re.MULTILINE)
            dependencies["includes"] = [inc.strip() for inc in pug_includes]

            # Pug blocks
            pug_blocks = re.findall(r"^(?:\s*)block\s+(\w+)", content, re.MULTILINE)
            dependencies["blocks"] = pug_blocks

            # Pug mixins used
            mixin_calls = re.findall(r"\+(\w+)\s*(?:\(|$)", content, re.MULTILINE)
            dependencies["mixin_calls"] = list(set(mixin_calls))

        else:
            # Try auto-detect
            if "<%=" in content or "<%-" in content:
                dependencies["template_engine"] = "ejs"
                ejs_include = re.findall(r"<%[-=]?\s*include\s*\(\s*['\"]([^'\"]+)['\"]", content)
                dependencies["includes"] = ejs_include
            elif "{{>" in content or "{{#" in content:
                dependencies["template_engine"] = "handlebars"
                hbs_partials = re.findall(r"\{\{>\s*(\S+?)(?:\s+[^}]*)?\}\}", content)
                dependencies["partials"] = hbs_partials

        # Recursively resolve includes
        resolved = set()
        dependency_tree = self._resolve_template_tree(base, template_path, dependencies, resolved)
        dependencies["dependency_tree"] = dependency_tree

        return {
            "status": "success",
            "template": dependencies,
        }

    def _resolve_template_tree(self, base: str, template_path: str,
                                deps: Dict[str, Any], resolved: Set[str]) -> List[Dict[str, Any]]:
        """Recursively resolve template dependency tree."""
        tree = []
        resolved.add(template_path)

        all_deps = deps.get("includes", []) + deps.get("partials", [])
        if deps.get("extends"):
            all_deps.append(deps["extends"])

        views_dir = os.path.dirname(os.path.join(base, template_path))

        for dep in all_deps:
            if dep in resolved:
                tree.append({"name": dep, "status": "circular_reference"})
                continue

            # Try to find the actual file
            possible_paths = [
                os.path.join(views_dir, dep),
                os.path.join(base, self.SCAN_DIRS["views"], dep),
                os.path.join(base, dep),
            ]
            # Add extensions
            extended = []
            for p in possible_paths:
                extended.append(p)
                for ext in [".ejs", ".hbs", ".handlebars", ".pug", ".html"]:
                    extended.append(p + ext)

            found = None
            for p in extended:
                if os.path.isfile(p):
                    found = os.path.relpath(p, base)
                    break

            if found:
                tree.append({"name": dep, "resolved_path": found, "status": "found"})
            else:
                tree.append({"name": dep, "status": "not_found"})

        return tree

    # ── 5. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace a complete request flow: Route -> middleware -> handler ->
        service -> model -> response.

        Args:
            entry_point: Controller/router name or route path (e.g., "UserController" or "/api/users")
            method: Optional HTTP method (GET, POST, etc.)
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        flows: List[Dict[str, Any]] = []

        # First, find matching routes
        route_map = self.get_route_map()
        if route_map.get("status") != "success":
            return route_map

        matching_routes = []
        for route in route_map.get("routes", []):
            path_match = entry_point.lower() in route["path"].lower()
            handler_match = any(entry_point.lower() in h.lower() for h in route.get("handlers", []))
            method_match = not method or route["method"].upper() == method.upper()

            if (path_match or handler_match) and method_match:
                matching_routes.append(route)

        if not matching_routes:
            return {"status": "error", "message": f"No routes found matching '{entry_point}'"}

        for route in matching_routes:
            flow: Dict[str, Any] = {
                "route": {
                    "method": route["method"],
                    "path": route["path"],
                    "file": route.get("file"),
                },
                "middleware": [],
                "handler": None,
                "service_calls": [],
                "model_operations": [],
                "response": None,
            }

            # Identify handler
            handlers = route.get("handlers", [])
            if handlers:
                flow["handler"] = handlers[-1] if len(handlers) > 0 else None
                flow["middleware"] = handlers[:-1] if len(handlers) > 1 else []

            # Trace handler to find service/model calls
            handler_name = flow["handler"] or ""
            for finfo in all_files:
                content = finfo["content"]
                if handler_name and handler_name in content:
                    # Look for service calls
                    service_calls = re.findall(
                        r"(?:new\s+)?(\w+Service)\s*(?:\(|\.)",
                        content,
                    )
                    flow["service_calls"] = list(set(service_calls))

                    # Look for model operations
                    model_ops = re.findall(
                        r"(?:\w+)\.(find|findOne|findById|findAll|create|update|delete|save|remove|destroy|aggregate|countDocuments|findByPk)\s*\(",
                        content,
                    )
                    flow["model_operations"] = list(set(model_ops))

                    # Look for response patterns
                    res_patterns = re.findall(
                        r"res\.(json|send|render|redirect|status|download|sendFile)\s*\(",
                        content,
                    )
                    flow["response"] = list(set(res_patterns)) if res_patterns else None
                    break

            flows.append(flow)

        return {
            "status": "success",
            "entry_point": entry_point,
            "method": method,
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 6. get_middleware_chain ────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse app.use() and router-level middleware. Extract ordered middleware
        stack and which paths they apply to.

        Args:
            route_path: Optional route path to filter middleware for.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        middleware_entries: List[Dict[str, Any]] = []

        # Global middleware: app.use(middleware)
        global_use_pattern = re.compile(
            r"(?:app|server)\s*\.use\s*\(\s*(?!['\"]\/)(\w[\w.()]*)",
            re.MULTILINE,
        )

        # Path-scoped middleware: app.use('/path', middleware)
        path_use_pattern = re.compile(
            r"(?:app|router|server)\s*\.use\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*(.+?)\s*\)",
            re.MULTILINE,
        )

        # Router-level middleware: router.use(middleware)
        router_use_pattern = re.compile(
            r"router\s*\.use\s*\(\s*(?!['\"]\/)(\w[\w.()]*)",
            re.MULTILINE,
        )

        order = 0
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Global middleware
            for match in global_use_pattern.finditer(content):
                mw_name = match.group(1).strip()
                if mw_name and not mw_name.startswith("//"):
                    order += 1
                    middleware_entries.append({
                        "order": order,
                        "name": mw_name,
                        "scope": "global",
                        "path": "*",
                        "file": rel_path,
                    })

            # Path-scoped middleware
            for match in path_use_pattern.finditer(content):
                path = match.group(1)
                mw_chain = match.group(2).strip()
                order += 1
                middleware_entries.append({
                    "order": order,
                    "name": mw_chain,
                    "scope": "path",
                    "path": path,
                    "file": rel_path,
                })

            # Router-level
            for match in router_use_pattern.finditer(content):
                mw_name = match.group(1).strip()
                if mw_name and not mw_name.startswith("//"):
                    order += 1
                    middleware_entries.append({
                        "order": order,
                        "name": mw_name,
                        "scope": "router",
                        "path": rel_path,
                        "file": rel_path,
                    })

        # Filter by route_path if provided
        if route_path:
            filtered = []
            for mw in middleware_entries:
                if mw["scope"] == "global":
                    filtered.append(mw)
                elif mw["scope"] == "path" and route_path.startswith(mw["path"]):
                    filtered.append(mw)
                elif mw["scope"] == "router" and route_path in mw.get("file", ""):
                    filtered.append(mw)
            middleware_entries = filtered

        return {
            "status": "success",
            "middleware": middleware_entries,
            "total": len(middleware_entries),
            "route_filter": route_path,
        }

    # ── 7. get_validation_chain ───────────────────────────────────────

    def get_validation_chain(self, route_path: str) -> Dict[str, Any]:
        """
        Trace validation chain: express-validator/Joi/Zod schema -> middleware -> handler.
        Detect mismatches between validated fields and used fields.

        Args:
            route_path: Route path to trace validation for (e.g., "/api/users").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        validation_info: Dict[str, Any] = {
            "route": route_path,
            "validation_library": None,
            "validated_fields": [],
            "handler_used_fields": [],
            "mismatches": [],
        }

        # Find route handler for this path
        route_map = self.get_route_map(route_path)
        matching_routes = route_map.get("routes", [])

        # Detect validation library and fields
        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # express-validator: body('field'), check('field'), param('field'), query('field')
            ev_fields = re.findall(
                r"(?:body|check|param|query)\s*\(\s*['\"](\w+)['\"]",
                content,
            )
            if ev_fields:
                validation_info["validation_library"] = "express-validator"
                for field in ev_fields:
                    field_info: Dict[str, Any] = {"name": field, "file": rel_path}
                    # Check for validation chains
                    chains = re.findall(
                        rf"(?:body|check|param|query)\s*\(\s*['\"]" + re.escape(field) +
                        r"['\"]\s*\)((?:\.\w+\([^)]*\))*)",
                        content,
                    )
                    if chains:
                        validators = re.findall(r"\.(\w+)\(", chains[0])
                        field_info["validators"] = validators
                    validation_info["validated_fields"].append(field_info)

            # Joi: Joi.object({ field: Joi.string() })
            joi_fields = re.findall(
                r"(\w+)\s*:\s*Joi\.(\w+)\s*\(",
                content,
            )
            if joi_fields:
                validation_info["validation_library"] = "joi"
                for field_name, field_type in joi_fields:
                    validation_info["validated_fields"].append({
                        "name": field_name,
                        "type": field_type,
                        "file": rel_path,
                    })

            # Zod: z.object({ field: z.string() })
            zod_fields = re.findall(
                r"(\w+)\s*:\s*z\.(\w+)\s*\(",
                content,
            )
            if zod_fields:
                validation_info["validation_library"] = "zod"
                for field_name, field_type in zod_fields:
                    validation_info["validated_fields"].append({
                        "name": field_name,
                        "type": field_type,
                        "file": rel_path,
                    })

            # Find fields used in handler (req.body.field, req.params.field, req.query.field)
            body_fields = re.findall(r"req\.body\.(\w+)", content)
            param_fields = re.findall(r"req\.params\.(\w+)", content)
            query_fields = re.findall(r"req\.query\.(\w+)", content)

            # Destructured: const { field1, field2 } = req.body
            destructured = re.findall(
                r"(?:const|let|var)\s*\{([^}]+)\}\s*=\s*req\.(body|params|query)",
                content,
            )
            for fields_str, source in destructured:
                for field in re.findall(r"(\w+)", fields_str):
                    if source == "body":
                        body_fields.append(field)
                    elif source == "params":
                        param_fields.append(field)
                    elif source == "query":
                        query_fields.append(field)

            for field in set(body_fields):
                validation_info["handler_used_fields"].append({"name": field, "source": "body", "file": rel_path})
            for field in set(param_fields):
                validation_info["handler_used_fields"].append({"name": field, "source": "params", "file": rel_path})
            for field in set(query_fields):
                validation_info["handler_used_fields"].append({"name": field, "source": "query", "file": rel_path})

        # Detect mismatches
        validated_names = {f["name"] for f in validation_info["validated_fields"]}
        used_names = {f["name"] for f in validation_info["handler_used_fields"]}

        unvalidated = used_names - validated_names
        unused_validations = validated_names - used_names

        if unvalidated:
            validation_info["mismatches"].append({
                "type": "unvalidated_fields",
                "fields": sorted(unvalidated),
                "severity": "warning",
                "message": "Fields used in handler but not validated",
            })
        if unused_validations:
            validation_info["mismatches"].append({
                "type": "unused_validations",
                "fields": sorted(unused_validations),
                "severity": "info",
                "message": "Validation rules defined but field not used in handler",
            })

        return {
            "status": "success",
            "validation": validation_info,
        }

    # ── 8. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify an endpoint: check route exists, handler defined, middleware chain,
        and error handling.

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
                if route["method"].upper() == method.upper() and self._paths_match(route["path"], url):
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
                "detail": f"Route {method.upper()} {url} not found in any route file",
            })
            return {"status": "success", "verification": verification}

        # 2. Check handler exists
        handlers = matched_route.get("handlers", [])
        if handlers:
            handler_name = handlers[-1]
            verification["checks"].append({
                "check": "handler_defined",
                "status": "pass",
                "detail": f"Handler: {handler_name}",
            })

            # Verify handler function exists in source
            all_files = self._scan_all_source(base)
            handler_found = False
            for finfo in all_files:
                # Look for function/const/class definition
                if re.search(
                    rf"(?:function\s+{re.escape(handler_name)}|const\s+{re.escape(handler_name)}\s*=|exports\.{re.escape(handler_name)}|module\.exports\s*=.*{re.escape(handler_name)})",
                    finfo["content"],
                ):
                    handler_found = True
                    verification["checks"].append({
                        "check": "handler_implementation",
                        "status": "pass",
                        "detail": f"Handler implemented in {finfo['rel_path']}",
                    })
                    # Check for error handling in handler
                    has_try_catch = "try" in finfo["content"] and "catch" in finfo["content"]
                    has_next_error = "next(err" in finfo["content"] or "next(error" in finfo["content"]
                    if has_try_catch or has_next_error:
                        verification["checks"].append({
                            "check": "error_handling",
                            "status": "pass",
                            "detail": "Handler has error handling (try/catch or next(err))",
                        })
                    else:
                        verification["warnings"].append({
                            "check": "error_handling",
                            "status": "warning",
                            "detail": "Handler may lack error handling (no try/catch or next(err))",
                        })
                    break

            if not handler_found:
                verification["warnings"].append({
                    "check": "handler_implementation",
                    "status": "warning",
                    "detail": f"Could not verify handler '{handler_name}' implementation in source files",
                })
        else:
            verification["warnings"].append({
                "check": "handler_defined",
                "status": "warning",
                "detail": "No handler identified for route",
            })

        # 3. Check middleware chain
        middleware = self.get_middleware_chain(url)
        if middleware.get("status") == "success":
            mw_list = middleware.get("middleware", [])
            verification["checks"].append({
                "check": "middleware_chain",
                "status": "pass",
                "detail": f"{len(mw_list)} middleware(s) apply to this route",
                "middleware": [mw["name"] for mw in mw_list[:10]],
            })

        return {
            "status": "success",
            "verification": verification,
        }

    def _paths_match(self, pattern: str, url: str) -> bool:
        """Check if a route pattern matches a URL, handling Express params (:id)."""
        # Convert Express params to regex
        regex_pattern = re.sub(r":(\w+)", r"[^/]+", pattern)
        regex_pattern = "^" + regex_pattern + "$"
        try:
            return bool(re.match(regex_pattern, url))
        except re.error:
            return pattern == url

    # ── 9. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the Express.js project codebase.

        Args:
            pattern_type: One of: "naming", "middleware", "error-handling", "auth", "testing"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_types = ["naming", "middleware", "error-handling", "auth", "testing"]
        if pattern_type not in valid_types:
            return {"status": "error", "message": f"Invalid pattern_type. Choose from: {valid_types}"}

        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}
        all_files = self._scan_all_source(base)

        if pattern_type == "naming":
            # Analyze naming patterns
            file_patterns: Dict[str, int] = {}
            export_patterns: Dict[str, int] = {}

            for finfo in all_files:
                fname = os.path.basename(finfo["rel_path"])
                # File naming: camelCase, PascalCase, kebab-case, snake_case
                if re.match(r"^[a-z][a-zA-Z]+\.\w+$", fname):
                    file_patterns["camelCase"] = file_patterns.get("camelCase", 0) + 1
                elif re.match(r"^[A-Z][a-zA-Z]+\.\w+$", fname):
                    file_patterns["PascalCase"] = file_patterns.get("PascalCase", 0) + 1
                elif re.match(r"^[a-z]+(-[a-z]+)+\.\w+$", fname):
                    file_patterns["kebab-case"] = file_patterns.get("kebab-case", 0) + 1
                elif re.match(r"^[a-z]+(_[a-z]+)+\.\w+$", fname):
                    file_patterns["snake_case"] = file_patterns.get("snake_case", 0) + 1

                # Export patterns
                if "module.exports" in finfo["content"]:
                    export_patterns["CommonJS"] = export_patterns.get("CommonJS", 0) + 1
                if "export default" in finfo["content"] or "export {" in finfo["content"]:
                    export_patterns["ESModule"] = export_patterns.get("ESModule", 0) + 1

            conventions["findings"].append({
                "category": "file_naming",
                "patterns": file_patterns,
                "dominant": max(file_patterns, key=file_patterns.get) if file_patterns else "unknown",
            })
            conventions["findings"].append({
                "category": "module_system",
                "patterns": export_patterns,
                "dominant": max(export_patterns, key=export_patterns.get) if export_patterns else "unknown",
            })

        elif pattern_type == "middleware":
            middleware_info = self.get_middleware_chain()
            if middleware_info.get("status") == "success":
                conventions["findings"].append({
                    "category": "middleware_stack",
                    "total": middleware_info.get("total", 0),
                    "middleware": middleware_info.get("middleware", [])[:15],
                })

        elif pattern_type == "error-handling":
            error_patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if re.search(r"try\s*\{", content):
                    error_patterns["try_catch"] = error_patterns.get("try_catch", 0) + 1
                if re.search(r"\.catch\s*\(", content):
                    error_patterns["promise_catch"] = error_patterns.get("promise_catch", 0) + 1
                if re.search(r"next\s*\(\s*(?:err|error|new\s+Error)", content):
                    error_patterns["next_error"] = error_patterns.get("next_error", 0) + 1
                if re.search(r"err\s*,\s*req\s*,\s*res\s*,\s*next", content):
                    error_patterns["error_middleware"] = error_patterns.get("error_middleware", 0) + 1
                if re.search(r"process\.on\s*\(\s*['\"]uncaughtException", content):
                    error_patterns["uncaught_handler"] = error_patterns.get("uncaught_handler", 0) + 1

            conventions["findings"].append({
                "category": "error_handling",
                "patterns": error_patterns,
            })

        elif pattern_type == "auth":
            auth_patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if "passport" in content.lower():
                    auth_patterns["passport"] = auth_patterns.get("passport", 0) + 1
                if "jsonwebtoken" in content or "jwt" in content.lower():
                    auth_patterns["jwt"] = auth_patterns.get("jwt", 0) + 1
                if "bcrypt" in content.lower():
                    auth_patterns["bcrypt"] = auth_patterns.get("bcrypt", 0) + 1
                if "session" in content.lower() and "express-session" in content:
                    auth_patterns["session"] = auth_patterns.get("session", 0) + 1
                if "oauth" in content.lower():
                    auth_patterns["oauth"] = auth_patterns.get("oauth", 0) + 1

            conventions["findings"].append({
                "category": "authentication",
                "patterns": auth_patterns,
            })

        elif pattern_type == "testing":
            test_patterns: Dict[str, int] = {}
            test_files = self._scan_files(base, self.SCAN_DIRS["tests"])
            if not test_files:
                # Also check root-level test dirs
                for d in ["test", "__tests__", "spec"]:
                    test_files.extend(self._scan_files(base, d))

            for finfo in test_files:
                content = finfo["content"]
                if "describe(" in content:
                    test_patterns["mocha_style"] = test_patterns.get("mocha_style", 0) + 1
                if "it(" in content or "test(" in content:
                    test_patterns["test_cases"] = test_patterns.get("test_cases", 0) + 1
                if "expect(" in content:
                    test_patterns["expect_assertions"] = test_patterns.get("expect_assertions", 0) + 1
                if "jest" in content.lower():
                    test_patterns["jest"] = test_patterns.get("jest", 0) + 1
                if "supertest" in content.lower() or "request(app)" in content:
                    test_patterns["supertest"] = test_patterns.get("supertest", 0) + 1
                if "sinon" in content.lower():
                    test_patterns["sinon_mocks"] = test_patterns.get("sinon_mocks", 0) + 1

            conventions["findings"].append({
                "category": "testing",
                "patterns": test_patterns,
                "test_files_found": len(test_files),
            })

        return {
            "status": "success",
            "conventions": conventions,
        }

    # ── 10. get_similar_patterns ──────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.

        Args:
            description: Natural language description (e.g., "file upload", "pagination").
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        description_lower = description.lower()
        matches: List[Dict[str, Any]] = []

        # Match against pattern registry
        matched_categories = []
        for category, info in self.PATTERN_REGISTRY.items():
            keywords = category.replace("-", " ").split()
            if any(kw in description_lower for kw in keywords) or category in description_lower:
                matched_categories.append(category)

        if not matched_categories:
            # Fuzzy match: check each word in description
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
                    found = re.findall(pattern, content)
                    if found:
                        # Get line numbers
                        for line_match in re.finditer(pattern, content):
                            line_num = content[:line_match.start()].count("\n") + 1
                            line_text = content.split("\n")[line_num - 1].strip()[:120]
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

    # ── 11. get_project_snapshot ──────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a project overview: routes, models, middleware, env vars,
        scripts, and dependencies.
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
            "scripts": {},
            "env_vars": [],
        }

        # Directory structure
        for category, directory in self.SCAN_DIRS.items():
            full_dir = os.path.join(base, directory)
            if os.path.isdir(full_dir):
                file_count = 0
                for root, _, files in os.walk(full_dir):
                    file_count += len([f for f in files if f.endswith((".js", ".ts", ".mjs"))])
                snapshot["structure"][category] = {
                    "path": directory,
                    "file_count": file_count,
                }

        # Routes summary
        route_map = self.get_route_map()
        if route_map.get("status") == "success":
            routes = route_map.get("routes", [])
            method_counts: Dict[str, int] = {}
            for r in routes:
                m = r["method"]
                method_counts[m] = method_counts.get(m, 0) + 1
            snapshot["routes_summary"] = {
                "total": len(routes),
                "by_method": method_counts,
            }

        # Models summary
        models = self.get_model_schema()
        if models.get("status") == "success":
            snapshot["models_summary"] = {
                "total": models.get("total_models", 0),
                "models": list(models.get("models", {}).keys()),
            }

        # Middleware summary
        middleware = self.get_middleware_chain()
        if middleware.get("status") == "success":
            snapshot["middleware_summary"] = {
                "total": middleware.get("total", 0),
            }

        # package.json
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            try:
                import json
                with open(pkg_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                snapshot["dependencies"] = {
                    "production": list(pkg.get("dependencies", {}).keys()),
                    "dev": list(pkg.get("devDependencies", {}).keys()),
                }
                snapshot["scripts"] = pkg.get("scripts", {})
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # .env variables (names only, not values)
        env_path = os.path.join(base, ".env")
        env_example_path = os.path.join(base, ".env.example")
        for ep in [env_example_path, env_path]:
            if os.path.isfile(ep):
                try:
                    with open(ep, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                var_name = line.split("=")[0].strip()
                                if var_name and var_name not in snapshot["env_vars"]:
                                    snapshot["env_vars"].append(var_name)
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

        return {
            "status": "success",
            "snapshot": snapshot,
        }

    # ── 12. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware checks before editing a symbol. Combines ripple effect
        analysis with Express.js-specific checks.

        Args:
            file_path: Relative file path (e.g., "src/controllers/userController.js")
            symbol_name: Symbol to check (e.g., "createUser", "authMiddleware")
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
            "symbol_found": False,
            "references": [],
            "impact": [],
            "suggestions": [],
            "anti_patterns": [],
        }

        # Check if symbol exists in file
        if symbol_name in content:
            checks["symbol_found"] = True

        # Find all references across project
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

        # Context-specific checks based on file type/location
        if "route" in file_path.lower() or "router" in file_path.lower():
            checks["impact"].append({
                "type": "route_change",
                "message": f"Changing '{symbol_name}' in a route file may affect endpoint availability",
                "severity": "high",
            })
            # Check if route is used in tests
            test_files = self._scan_files(base, self.SCAN_DIRS["tests"])
            for tf in test_files:
                if symbol_name in tf["content"]:
                    checks["impact"].append({
                        "type": "test_impact",
                        "message": f"Tests reference this symbol: {tf['rel_path']}",
                        "severity": "medium",
                    })

        elif "middleware" in file_path.lower():
            checks["impact"].append({
                "type": "middleware_change",
                "message": f"Changing middleware '{symbol_name}' affects all routes using it",
                "severity": "high",
            })

        elif "model" in file_path.lower():
            checks["impact"].append({
                "type": "model_change",
                "message": f"Changing model '{symbol_name}' may require migration updates",
                "severity": "high",
            })

        elif "controller" in file_path.lower():
            checks["impact"].append({
                "type": "controller_change",
                "message": f"Changing controller '{symbol_name}' may affect route handlers",
                "severity": "medium",
            })

        elif "service" in file_path.lower():
            checks["impact"].append({
                "type": "service_change",
                "message": f"Changing service '{symbol_name}' may affect controllers using it",
                "severity": "medium",
            })

        # Anti-pattern detection in the target file
        for ap in self.ANTI_PATTERNS:
            if re.search(ap["pattern"], content):
                checks["anti_patterns"].append({
                    "pattern": ap["pattern"],
                    "severity": ap["severity"],
                    "message": ap["message"],
                })

        # Check for exports
        if re.search(rf"(?:module\.exports|export\s+(?:default\s+)?(?:function\s+|const\s+|class\s+)?){re.escape(symbol_name)}", content):
            checks["suggestions"].append(
                f"'{symbol_name}' is exported -- changes will affect importing modules"
            )

        return {
            "status": "success",
            "checks": checks,
            "total_references": sum(r.get("total", 0) for r in checks["references"]),
        }

    # -----------------------------------------------------------------
    # verify_schema
    # -----------------------------------------------------------------
    def verify_schema(self, entity_or_model: str, fields: list) -> Dict[str, Any]:
        """Verify fields exist in Mongoose Schema, Sequelize define(), or TypeORM @Column."""
        base = self.base_path
        result: Dict[str, Any] = {
            "status": "success",
            "model": entity_or_model,
            "requested_fields": fields,
            "found_fields": [],
            "missing_fields": [],
            "source_file": None,
            "warnings": [],
        }

        model_dirs = [
            os.path.join(base, "src", "models"),
            os.path.join(base, "models"),
            os.path.join(base, "src", "entities"),
            os.path.join(base, "entities"),
        ]

        model_found = False
        all_schema_fields: set = set()

        for mdir in model_dirs:
            if not os.path.isdir(mdir):
                continue
            for dirpath, _, filenames in os.walk(mdir):
                for fn in filenames:
                    if not fn.endswith(('.js', '.ts', '.mjs')):
                        continue
                    fpath = os.path.join(dirpath, fn)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                    except Exception:
                        continue

                    # Check if file defines the requested model
                    model_patterns = [
                        rf'mongoose\.model\s*\(\s*["\']?{re.escape(entity_or_model)}',
                        rf'new\s+Schema\s*\(.*?{re.escape(entity_or_model)}',
                        rf'\.define\s*\(\s*["\']?{re.escape(entity_or_model)}',
                        rf'class\s+{re.escape(entity_or_model)}\s+extends',
                        rf'{re.escape(entity_or_model)}Schema\s*=',
                    ]
                    if not any(re.search(p, content, re.IGNORECASE) for p in model_patterns):
                        # Also check filename
                        base_fn = fn.replace('.ts', '').replace('.js', '').replace('.mjs', '').lower()
                        if entity_or_model.lower() not in base_fn:
                            continue

                    model_found = True
                    result["source_file"] = os.path.relpath(fpath, base)

                    # Mongoose Schema fields: key: { type: ... } or key: Type
                    mongoose_fields = re.findall(r'(\w+)\s*:\s*(?:\{[^}]*type\s*:|String|Number|Boolean|Date|ObjectId|Array|Buffer|Map|Schema)', content)
                    all_schema_fields.update(mongoose_fields)

                    # Sequelize define fields
                    seq_fields = re.findall(r'(\w+)\s*:\s*\{[^}]*type\s*:\s*DataTypes\.', content)
                    all_schema_fields.update(seq_fields)

                    # TypeORM @Column
                    typeorm_fields = re.findall(r'@Column\s*\([^)]*\)\s*\n?\s*(\w+)', content)
                    all_schema_fields.update(typeorm_fields)

                    # Plain object keys as fallback
                    plain_keys = re.findall(r'^\s+(\w+)\s*:', content, re.MULTILINE)
                    all_schema_fields.update(plain_keys)

        if not model_found:
            result["warnings"].append(f"Model '{entity_or_model}' not found in model directories")
            result["missing_fields"] = list(fields)
        else:
            for fld in fields:
                if fld in all_schema_fields:
                    result["found_fields"].append(fld)
                else:
                    result["missing_fields"].append(fld)

        return result

    # -----------------------------------------------------------------
    # detect_transaction_risks
    # -----------------------------------------------------------------
    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing transaction wrappers around multiple DB writes."""
        base = self.base_path
        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "risks": [],
            "suggestions": [],
        }
        if not os.path.isfile(full_path):
            result["status"] = "error"
            result["error"] = f"File not found: {file_path}"
            return result

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            return result

        write_ops_re = re.compile(r'\.\s*(?:save|create|update|delete|destroy|remove|insertMany|updateMany|deleteMany|bulkCreate|bulkWrite)\s*\(')
        transaction_re = re.compile(r'(?:\.startSession|\.startTransaction|sequelize\.transaction|\.createQueryRunner|withTransaction|session\s*:\s*\w+)')
        cache_ops_re = re.compile(r'(?:redis\.|cache\.|\.setex\s*\(|\.set\s*\(|\.del\s*\(|ioredis|memcached)')

        # Split into functions/methods
        func_re = re.compile(
            r'(?:async\s+)?(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?|(\w+)\s*(?::\s*\w+)?\s*(?:=\s*)?async\s*\(|(\w+)\s*\([^)]*\)\s*\{)',
            re.MULTILINE
        )
        functions = list(func_re.finditer(content))

        for i, m in enumerate(functions):
            func_name = m.group(1) or m.group(2) or m.group(3) or m.group(4) or "anonymous"
            start = m.start()
            end = functions[i + 1].start() if i + 1 < len(functions) else len(content)
            block = content[start:end]

            writes = write_ops_re.findall(block)
            has_transaction = bool(transaction_re.search(block))
            has_cache = bool(cache_ops_re.search(block))

            if len(writes) >= 2 and not has_transaction:
                result["risks"].append({
                    "function": func_name,
                    "type": "missing_transaction",
                    "write_count": len(writes),
                    "message": f"Function '{func_name}' has {len(writes)} write operations without a transaction wrapper",
                    "severity": "high",
                })

            if has_transaction and has_cache:
                result["risks"].append({
                    "function": func_name,
                    "type": "cache_in_transaction",
                    "message": f"Function '{func_name}' mixes cache operations with DB transaction -- risk of inconsistency on rollback",
                    "severity": "medium",
                })

        if not result["risks"]:
            result["suggestions"].append("No transaction risks detected")

        return result

    # -----------------------------------------------------------------
    # get_domain_rules
    # -----------------------------------------------------------------
    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Return Express domain-layer rules for the given file."""
        base = self.base_path
        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "file_type": "unknown",
            "violations": [],
            "rules_checked": [],
        }
        if not os.path.isfile(full_path):
            result["status"] = "error"
            result["error"] = f"File not found: {file_path}"
            return result

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            return result

        # Classify file type
        lower_path = file_path.lower()
        if '/route' in lower_path or 'router' in content[:500].lower():
            ftype = "route"
        elif '/controller' in lower_path:
            ftype = "controller"
        elif '/model' in lower_path or '/entit' in lower_path:
            ftype = "model"
        elif '/middleware' in lower_path:
            ftype = "middleware"
        else:
            ftype = "other"
        result["file_type"] = ftype

        if ftype == "route":
            result["rules_checked"].append("Routes must use validation middleware")
            route_methods = re.findall(r'(?:router|app)\s*\.\s*(?:get|post|put|patch|delete)\s*\(\s*["\'][^"\']+["\']\s*,\s*(\w+)\s*\)', content)
            # Routes with body methods but no validation middleware
            body_routes = re.finditer(r'(?:router|app)\s*\.\s*(?:post|put|patch)\s*\(\s*["\'][^"\']+["\']\s*,([^)]+)\)', content)
            for m in body_routes:
                handler_chain = m.group(1)
                if not re.search(r'(?:validate|validation|check|body\s*\(|param\s*\(|joi|zod|yup|express-validator)', handler_chain, re.IGNORECASE):
                    result["violations"].append({
                        "rule": "missing_validation_middleware",
                        "message": f"POST/PUT/PATCH route without validation middleware: {m.group(0).strip()[:80]}",
                        "severity": "medium",
                    })

        elif ftype == "controller":
            result["rules_checked"].append("Controllers must have error handling")
            # Check for try/catch or error middleware
            func_re = re.compile(r'(?:async\s+)?(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=)', re.MULTILINE)
            funcs = list(func_re.finditer(content))
            for i, fm in enumerate(funcs):
                fname = fm.group(1) or fm.group(2) or "anonymous"
                start = fm.start()
                end = funcs[i + 1].start() if i + 1 < len(funcs) else len(content)
                block = content[start:end]
                if 'async' in block[:50] and 'try' not in block and 'catch' not in block and 'wrapper' not in block.lower():
                    result["violations"].append({
                        "rule": "missing_error_handling",
                        "message": f"Async controller '{fname}' has no try/catch error handling",
                        "severity": "medium",
                    })

        elif ftype == "model":
            result["rules_checked"].append("Models must include field validation")
            if not re.search(r'(?:required\s*:\s*true|validate\s*:|validator|min\s*:|max\s*:|minlength|maxlength|enum\s*:)', content, re.IGNORECASE):
                result["violations"].append({
                    "rule": "missing_model_validation",
                    "message": "Model has no field validations (required, min, max, enum, etc.)",
                    "severity": "medium",
                })

        elif ftype == "middleware":
            result["rules_checked"].append("Middleware must call next()")
            # Check exported functions for next() call
            func_blocks = re.findall(r'(?:module\.exports|export)\s*=.*?(?=module\.exports|export|$)', content, re.DOTALL)
            if not func_blocks:
                func_blocks = [content]
            for block in func_blocks:
                if re.search(r'(?:req|request)\s*,\s*(?:res|response)\s*,\s*next', block) and 'next(' not in block:
                    result["violations"].append({
                        "rule": "missing_next_call",
                        "message": "Middleware accepts next parameter but never calls next()",
                        "severity": "high",
                    })

        return result

    # -----------------------------------------------------------------
    # generate_test_skeleton
    # -----------------------------------------------------------------
    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Generate a Jest/Mocha test skeleton for an Express module."""
        base = self.base_path
        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "symbol": symbol,
            "test_skeleton": "",
            "test_file_path": "",
            "framework": "jest",
        }
        if not os.path.isfile(full_path):
            result["status"] = "error"
            result["error"] = f"File not found: {file_path}"
            return result

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            return result

        # Detect if it uses TypeScript
        is_ts = file_path.endswith('.ts')
        ext = '.test.ts' if is_ts else '.test.js'

        # Determine test path
        test_path = file_path
        for prefix in ['src/', 'lib/']:
            if test_path.startswith(prefix):
                test_path = test_path.replace(prefix, '__tests__/', 1)
                break
        else:
            test_path = '__tests__/' + test_path
        test_path = re.sub(r'\.(js|ts|mjs)$', ext, test_path)
        result["test_file_path"] = test_path

        # Detect imports/requires for mocking
        imports = re.findall(r"(?:require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)|from\s+['\"]([^'\"]+)['\"])", content)
        mock_modules = [i[0] or i[1] for i in imports if not (i[0] or i[1]).startswith('.')]

        # Detect exported functions
        exported_funcs = re.findall(r'(?:module\.exports\s*=\s*\{([^}]+)\}|export\s+(?:const|function|async\s+function)\s+(\w+))', content, re.DOTALL)
        func_names = []
        for grp in exported_funcs:
            if grp[0]:
                func_names.extend(re.findall(r'(\w+)', grp[0]))
            if grp[1]:
                func_names.append(grp[1])

        # Classify for supertest usage
        lower_path = file_path.lower()
        uses_supertest = '/route' in lower_path or '/controller' in lower_path or 'app' in lower_path

        mock_lines = "\n".join(f"jest.mock('{mod}');" for mod in mock_modules[:5])
        import_path = './' + os.path.relpath(
            full_path.replace(base + '/', ''),
            os.path.dirname(test_path)
        ).replace('\\', '/')
        import_path = re.sub(r'\.(js|ts|mjs)$', '', import_path)

        supertest_block = ""
        if uses_supertest:
            supertest_block = """const request = require('supertest');
const app = require('../app'); // adjust path as needed
"""

        skeleton = f"""const {{ {', '.join(func_names[:5]) if func_names else symbol} }} = require('{import_path}');
{supertest_block}
{mock_lines}

describe('{symbol}', () => {{
    beforeEach(() => {{
        jest.clearAllMocks();
    }});
"""
        if uses_supertest:
            skeleton += f"""
    describe('HTTP endpoints', () => {{
        it('should respond with 200', async () => {{
            const res = await request(app)
                .get('/TODO')
                .expect(200);
            expect(res.body).toBeDefined();
        }});
    }});
"""
        else:
            skeleton += f"""
    describe('{symbol}', () => {{
        it('should return expected result', async () => {{
            // Arrange
            // TODO: set up test data and mocks

            // Act
            const result = await {symbol}(/* args */);

            // Assert
            expect(result).toBeDefined();
        }});

        it('should handle errors gracefully', async () => {{
            // Arrange
            // TODO: set up error condition

            // Act & Assert
            await expect({symbol}(/* args */)).rejects.toThrow();
        }});
    }});
"""
        skeleton += "});\n"
        result["test_skeleton"] = skeleton
        return result

    # -----------------------------------------------------------------
    # match_view_guards
    # -----------------------------------------------------------------
    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match Express middleware auth checks to SSR template conditional renders."""
        base = self.base_path
        full_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "symbol": symbol,
            "backend_guards": [],
            "view_guards": [],
            "mismatches": [],
        }
        if not os.path.isfile(full_path):
            result["status"] = "error"
            result["error"] = f"File not found: {file_path}"
            return result

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            return result

        # Extract middleware auth checks
        auth_middleware_re = re.compile(r'(?:isAuthenticated|isAdmin|requireAuth|requireLogin|ensureAuth|passport\.authenticate|isLoggedIn|checkRole|requireRole|hasRole)\s*(?:\(\s*["\']?(\w+)?["\']?\s*\))?')
        role_check_re = re.compile(r'(?:req\.user\.role\s*===?\s*["\'](\w+)["\']|req\.user\.(?:isAdmin|is_admin)|req\.session\.(?:user|isAuthenticated))')

        roles_backend: set = set()
        for m in auth_middleware_re.finditer(content):
            guard_name = m.group(0).split('(')[0].strip()
            role = m.group(1) if m.group(1) else None
            result["backend_guards"].append({"type": "middleware", "guard": guard_name, "role": role})
            if role:
                roles_backend.add(role)
        for m in role_check_re.finditer(content):
            if m.group(1):
                roles_backend.add(m.group(1))
                result["backend_guards"].append({"type": "role_check", "role": m.group(1)})

        # Scan EJS/Handlebars/Pug templates
        view_dirs = [
            os.path.join(base, "views"),
            os.path.join(base, "src", "views"),
        ]
        roles_frontend: set = set()
        for vdir in view_dirs:
            if not os.path.isdir(vdir):
                continue
            for dirpath, _, filenames in os.walk(vdir):
                for fn in filenames:
                    if not fn.endswith(('.ejs', '.hbs', '.handlebars', '.pug', '.html')):
                        continue
                    tpath = os.path.join(dirpath, fn)
                    try:
                        with open(tpath, 'r', encoding='utf-8', errors='replace') as f:
                            tcontent = f.read()
                    except Exception:
                        continue
                    rel_path = os.path.relpath(tpath, base)
                    # EJS: <% if (user.role === 'admin') %>
                    ejs_guards = re.findall(r'<%[-=]?\s*if\s*\(\s*(?:user|currentUser|req\.user)\.(\w+)', tcontent)
                    for g in ejs_guards:
                        result["view_guards"].append({"file": rel_path, "type": "ejs_condition", "field": g})
                        roles_frontend.add(g)
                    # Handlebars: {{#if user.isAdmin}}
                    hbs_guards = re.findall(r'\{\{#if\s+(?:user|currentUser)\.(\w+)', tcontent)
                    for g in hbs_guards:
                        result["view_guards"].append({"file": rel_path, "type": "handlebars_condition", "field": g})
                        roles_frontend.add(g)

        backend_only = roles_backend - roles_frontend
        frontend_only = roles_frontend - roles_backend
        for role in backend_only:
            result["mismatches"].append({"role": role, "issue": "Backend auth check has no matching view condition"})
        for role in frontend_only:
            result["mismatches"].append({"role": role, "issue": "View condition has no matching backend auth check"})

        return result
