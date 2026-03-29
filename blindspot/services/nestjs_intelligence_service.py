"""
NestJS Intelligence Service - NestJS/TypeScript-specific code analysis.

Provides module dependency graphing, decorator-based endpoint parsing,
TypeORM/Prisma entity mapping, guard/pipe/interceptor resolution,
DI graph building, validation chain tracing, and NestJS-specific flow mapping.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class NestJSIntelligenceService(BaseService):
    """NestJS-specific code intelligence for TypeScript/Node.js projects."""

    SCAN_DIRS = {
        "modules": "src",
        "controllers": "src",
        "services": "src",
        "entities": "src",
        "guards": "src/guards",
        "pipes": "src/pipes",
        "interceptors": "src/interceptors",
        "middleware": "src/middleware",
        "dtos": "src",
        "tests": "test",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bconsole\.(log|warn|error|debug|info)\s*\(",
            "severity": "warning",
            "message": "console.log in production code -- use NestJS Logger service instead",
            "file_types": ["ts"],
        },
        {
            "pattern": r":\s*any\b",
            "severity": "warning",
            "message": "Using 'any' type -- prefer explicit typing for type safety",
            "file_types": ["ts"],
        },
        {
            "pattern": r"@Inject\s*\([^)]*\)\s*(?!.*@Injectable)",
            "severity": "warning",
            "message": "@Inject without @Injectable on class -- may cause DI errors",
            "file_types": ["ts"],
        },
        {
            "pattern": r"forwardRef\s*\(\s*\(\)\s*=>",
            "severity": "warning",
            "message": "forwardRef detected -- possible circular dependency",
            "file_types": ["ts"],
        },
    ]

    PATTERN_REGISTRY = {
        "guard": {
            "patterns": [
                r"@UseGuards\s*\(",
                r"implements\s+CanActivate",
                r"canActivate\s*\(",
            ],
            "description": "NestJS guard patterns (authentication/authorization)",
        },
        "interceptor": {
            "patterns": [
                r"@UseInterceptors\s*\(",
                r"implements\s+NestInterceptor",
                r"intercept\s*\(\s*context.*next",
            ],
            "description": "NestJS interceptor patterns (logging, caching, transform)",
        },
        "pipe": {
            "patterns": [
                r"@UsePipes\s*\(",
                r"implements\s+PipeTransform",
                r"transform\s*\(\s*value",
            ],
            "description": "NestJS pipe patterns (validation, transformation)",
        },
        "filter": {
            "patterns": [
                r"@Catch\s*\(",
                r"implements\s+ExceptionFilter",
                r"catch\s*\(\s*exception",
            ],
            "description": "NestJS exception filter patterns",
        },
        "gateway": {
            "patterns": [
                r"@WebSocketGateway\s*\(",
                r"@SubscribeMessage\s*\(",
                r"implements\s+OnGatewayInit",
            ],
            "description": "NestJS WebSocket gateway patterns",
        },
        "microservice": {
            "patterns": [
                r"@MessagePattern\s*\(",
                r"@EventPattern\s*\(",
                r"ClientProxy",
                r"Transport\.\w+",
            ],
            "description": "NestJS microservice patterns (message/event patterns)",
        },
        "scheduler": {
            "patterns": [
                r"@Cron\s*\(",
                r"@Interval\s*\(",
                r"@Timeout\s*\(",
                r"ScheduleModule",
            ],
            "description": "NestJS scheduler/cron job patterns",
        },
        "queue": {
            "patterns": [
                r"@Process\s*\(",
                r"@Processor\s*\(",
                r"BullModule",
                r"InjectQueue\s*\(",
            ],
            "description": "NestJS queue/Bull job patterns",
        },
    }

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the NestJS project root path.

        In a monorepo, finds the workspace containing NestJS (looks for
        @nestjs/core in package.json). Falls back to base_path.
        """
        try:
            base = self.base_path
            if not base or not os.path.isdir(base):
                return None

            # Check if base itself is a NestJS project
            pkg = os.path.join(base, "package.json")
            if os.path.isfile(pkg):
                try:
                    import json
                    with open(pkg, "r") as f:
                        data = json.load(f)
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    if "@nestjs/core" in deps:
                        return base
                except Exception:
                    pass

            # Monorepo: search common workspace directories
            for name in ["backend", "api", "server", "services", "packages"]:
                sub = os.path.join(base, name)
                sub_pkg = os.path.join(sub, "package.json")
                if os.path.isfile(sub_pkg):
                    try:
                        import json
                        with open(sub_pkg, "r") as f:
                            data = json.load(f)
                        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                        if "@nestjs/core" in deps:
                            return sub
                    except Exception:
                        continue

            return base
        except Exception:
            pass
        return None

    def _scan_files(self, base: str, directory: str,
                    extensions: tuple = (".ts",)) -> List[dict]:
        """Scan directory recursively for TypeScript files."""
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in (
                "node_modules", ".git", "dist", "coverage", ".next", ".nuxt"
            )]
            for fname in files:
                if not fname.endswith(extensions):
                    continue
                # Skip compiled JS, declaration files, and spec files for non-test scans
                if fname.endswith(".d.ts") or fname.endswith(".js.map"):
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

    def _scan_all_ts_files(self, base: str) -> List[dict]:
        """Scan all TypeScript files in the project."""
        all_files: List[dict] = []
        seen: Set[str] = set()

        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        extra_dirs = ["libs", "apps", "packages"]
        for d in extra_dirs:
            for f in self._scan_files(base, d):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    def _extract_decorator_args(self, content: str, decorator_name: str) -> List[str]:
        """Extract arguments from a decorator like @Module({...})."""
        results = []
        pattern = re.compile(rf"@{decorator_name}\s*\(\s*(\{{)", re.DOTALL)
        for m in pattern.finditer(content):
            brace_start = m.start(1)
            depth = 0
            pos = brace_start
            while pos < len(content):
                if content[pos] == "{":
                    depth += 1
                elif content[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        results.append(content[brace_start:pos + 1])
                        break
                pos += 1
        return results

    def _extract_array_items(self, block: str, key: str) -> List[str]:
        """Extract items from a key: [...] array inside a decorator object."""
        pattern = re.compile(rf"{key}\s*:\s*\[([^\]]*)\]", re.DOTALL)
        m = pattern.search(block)
        if not m:
            return []
        items_raw = m.group(1)
        items = []
        for item in re.findall(r"(\w+)", items_raw):
            items.append(item)
        return items

    # ── 1. get_module_map ──────────────────────────────────────────────

    def get_module_map(self, module_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @Module() decorator: imports, controllers, providers, exports.
        Build module dependency graph.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        modules: Dict[str, Any] = {}

        module_class_pattern = re.compile(
            r"@Module\s*\(\s*(\{)",
            re.DOTALL,
        )
        class_name_pattern = re.compile(
            r"export\s+class\s+(\w+)",
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Find @Module decorator
            for mm in module_class_pattern.finditer(content):
                # Extract the full decorator argument object
                brace_start = mm.start(1)
                depth = 0
                pos = brace_start
                while pos < len(content):
                    if content[pos] == "{":
                        depth += 1
                    elif content[pos] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    pos += 1
                module_block = content[brace_start:pos + 1]

                # Find the class name after the decorator
                rest = content[pos:]
                cn = class_name_pattern.search(rest)
                if not cn:
                    continue
                class_name = cn.group(1)

                if module_name and class_name.lower() != module_name.lower():
                    continue

                # Parse module metadata
                imports = self._extract_array_items(module_block, "imports")
                controllers = self._extract_array_items(module_block, "controllers")
                providers = self._extract_array_items(module_block, "providers")
                exports = self._extract_array_items(module_block, "exports")

                modules[class_name] = {
                    "file": rel_path,
                    "imports": imports,
                    "controllers": controllers,
                    "providers": providers,
                    "exports": exports,
                }

        # Build dependency graph
        dependency_graph: Dict[str, List[str]] = {}
        for mod_name, mod_info in modules.items():
            dependency_graph[mod_name] = mod_info["imports"]

        return {
            "status": "ok",
            "modules": modules,
            "dependency_graph": dependency_graph,
            "total_modules": len(modules),
        }

    # ── 2. get_endpoint_map ────────────────────────────────────────────

    def get_endpoint_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @Controller() + @Get/@Post/@Put/@Delete/@Patch decorators.
        Extract path, params, guards, interceptors, pipes.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        endpoints: List[Dict[str, Any]] = []

        # Controller decorator
        controller_pattern = re.compile(
            r"@Controller\s*\(\s*(?:'([^']*)'|\"([^\"]*)\")?\s*\)",
        )

        # HTTP method decorators
        http_decorators = re.compile(
            r"@(Get|Post|Put|Delete|Patch|Head|Options|All)\s*\(\s*(?:'([^']*)'|\"([^\"]*)\")?",
        )

        # Guards, interceptors, pipes on method
        use_guards = re.compile(r"@UseGuards\s*\(\s*([^)]+)\)")
        use_interceptors = re.compile(r"@UseInterceptors\s*\(\s*([^)]+)\)")
        use_pipes = re.compile(r"@UsePipes\s*\(\s*([^)]+)\)")

        # Param decorators
        param_decorators = re.compile(
            r"@(Param|Query|Body|Headers|Req|Res|Session|Ip)\s*\(\s*(?:'([^']*)'|\"([^\"]*)\")?\s*\)",
        )

        # Method signature
        method_sig = re.compile(
            r"(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*([\w<>\[\]|]+))?\s*\{",
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Find controller class
            ctrl_m = controller_pattern.search(content)
            if not ctrl_m:
                continue

            ctrl_path = ctrl_m.group(1) or ctrl_m.group(2) or ""

            # Find class name
            class_m = re.search(r"export\s+class\s+(\w+)", content[ctrl_m.end():])
            if not class_m:
                continue
            ctrl_name = class_m.group(1)

            # Controller-level guards
            pre_class = content[max(0, ctrl_m.start() - 500):ctrl_m.start()]
            ctrl_guards = []
            for gm in use_guards.finditer(pre_class):
                ctrl_guards.extend([g.strip() for g in gm.group(1).split(",") if g.strip()])

            # Parse methods with HTTP decorators
            lines = content.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                hm = http_decorators.search(stripped)
                if not hm:
                    continue

                http_method = hm.group(1).upper()
                method_path = hm.group(2) or hm.group(3) or ""

                # Collect decorators from lines above this method
                method_guards = list(ctrl_guards)
                method_interceptors = []
                method_pipes = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    gm = use_guards.search(next_line)
                    if gm:
                        method_guards.extend(
                            [g.strip() for g in gm.group(1).split(",") if g.strip()]
                        )
                    im = use_interceptors.search(next_line)
                    if im:
                        method_interceptors.extend(
                            [i_name.strip() for i_name in im.group(1).split(",") if i_name.strip()]
                        )
                    pm = use_pipes.search(next_line)
                    if pm:
                        method_pipes.extend(
                            [p.strip() for p in pm.group(1).split(",") if p.strip()]
                        )

                    # Find the actual method signature
                    sig = method_sig.search(next_line)
                    if sig:
                        method_name = sig.group(1)
                        params_raw = sig.group(2)
                        return_type = sig.group(3) or "any"

                        # Parse parameters
                        params = []
                        for pd in param_decorators.finditer(params_raw):
                            param_source = pd.group(1)
                            param_key = pd.group(2) or pd.group(3)
                            params.append({
                                "source": param_source,
                                "key": param_key,
                            })

                        # Build full route
                        full_route = ""
                        if ctrl_path:
                            full_route = "/" + ctrl_path.strip("/")
                        if method_path:
                            full_route += "/" + method_path.strip("/")
                        if not full_route:
                            full_route = "/"

                        endpoint: Dict[str, Any] = {
                            "route": full_route,
                            "http_method": http_method,
                            "controller": ctrl_name,
                            "handler": method_name,
                            "return_type": return_type,
                            "parameters": params,
                            "file": rel_path,
                        }
                        if method_guards:
                            endpoint["guards"] = method_guards
                        if method_interceptors:
                            endpoint["interceptors"] = method_interceptors
                        if method_pipes:
                            endpoint["pipes"] = method_pipes

                        if filter_prefix and not full_route.startswith(filter_prefix):
                            break

                        endpoints.append(endpoint)
                        break

                    if re.match(r"(export\s+class|@Module|@Controller)", next_line):
                        break
                    j += 1

        return {
            "status": "ok",
            "endpoints": endpoints,
            "total": len(endpoints),
        }

    # ── 3. get_entity_relationships ────────────────────────────────────

    def get_entity_relationships(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse TypeORM @Entity: @OneToMany/@ManyToOne/@ManyToMany/@OneToOne,
        @Column, @PrimaryGeneratedColumn. Also Prisma model support.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        entities: Dict[str, Any] = {}

        # TypeORM patterns
        entity_decorator = re.compile(r"@Entity\s*\(\s*(?:'([^']*)'|\"([^\"]*)\")?")
        column_decorator = re.compile(
            r"@Column\s*\(\s*(\{[^}]*\})?\s*\)\s*\n?\s*(\w+)\s*(?::\s*([\w\[\]<>|]+))?",
            re.DOTALL,
        )
        primary_col = re.compile(
            r"@PrimaryGeneratedColumn\s*\(\s*(?:'([^']*)')?\s*\)\s*\n?\s*(\w+)\s*:",
        )
        primary_col_uuid = re.compile(
            r"@PrimaryColumn\s*\(\s*\)\s*\n?\s*(\w+)\s*:",
        )

        # Relationship decorators
        one_to_many = re.compile(
            r"@OneToMany\s*\(\s*\(\)\s*=>\s*(\w+)\s*,?\s*(?:\(\w+\)\s*=>\s*\w+\.(\w+))?"
            r"[^)]*\)\s*\n?\s*(\w+)\s*(?::\s*([\w\[\]<>|]+))?",
            re.DOTALL,
        )
        many_to_one = re.compile(
            r"@ManyToOne\s*\(\s*\(\)\s*=>\s*(\w+)\s*,?\s*(?:\(\w+\)\s*=>\s*\w+\.(\w+))?"
            r"[^)]*\)\s*\n?\s*(\w+)\s*(?::\s*([\w\[\]<>|]+))?",
            re.DOTALL,
        )
        many_to_many = re.compile(
            r"@ManyToMany\s*\(\s*\(\)\s*=>\s*(\w+)\s*,?\s*(?:\(\w+\)\s*=>\s*\w+\.(\w+))?"
            r"[^)]*\)\s*\n?\s*(\w+)\s*(?::\s*([\w\[\]<>|]+))?",
            re.DOTALL,
        )
        one_to_one = re.compile(
            r"@OneToOne\s*\(\s*\(\)\s*=>\s*(\w+)\s*,?\s*(?:\(\w+\)\s*=>\s*\w+\.(\w+))?"
            r"[^)]*\)\s*\n?\s*(\w+)\s*(?::\s*([\w\[\]<>|]+))?",
            re.DOTALL,
        )
        join_column = re.compile(r"@JoinColumn\s*\(")
        join_table = re.compile(r"@JoinTable\s*\(")

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Check for TypeORM entities
            for em in entity_decorator.finditer(content):
                table_name = em.group(1) or em.group(2)

                # Find class name
                class_search = content[em.end():]
                cn = re.search(r"export\s+class\s+(\w+)", class_search)
                if not cn:
                    continue
                class_name = cn.group(1)

                if entity_name and class_name.lower() != entity_name.lower():
                    continue

                entity_info: Dict[str, Any] = {
                    "file": rel_path,
                    "table_name": table_name,
                    "columns": [],
                    "relationships": [],
                    "primary_key": None,
                }

                # Parse primary key
                for pk in primary_col.finditer(content):
                    strategy = pk.group(1) or "increment"
                    pk_name = pk.group(2)
                    entity_info["primary_key"] = {
                        "name": pk_name,
                        "strategy": strategy,
                    }

                for pk in primary_col_uuid.finditer(content):
                    entity_info["primary_key"] = {
                        "name": pk.group(1),
                        "strategy": "manual",
                    }

                # Parse columns
                for col in column_decorator.finditer(content):
                    options_raw = col.group(1) or "{}"
                    col_name = col.group(2)
                    col_type_ts = col.group(3) or "unknown"
                    col_info: Dict[str, Any] = {
                        "name": col_name,
                        "ts_type": col_type_ts,
                    }
                    # Parse column options
                    nullable = re.search(r"nullable\s*:\s*(true|false)", options_raw)
                    if nullable:
                        col_info["nullable"] = nullable.group(1) == "true"
                    unique = re.search(r"unique\s*:\s*true", options_raw)
                    if unique:
                        col_info["unique"] = True
                    default_val = re.search(r"default\s*:\s*([^,}]+)", options_raw)
                    if default_val:
                        col_info["default"] = default_val.group(1).strip()
                    col_type_db = re.search(r"type\s*:\s*'([^']+)'", options_raw)
                    if col_type_db:
                        col_info["db_type"] = col_type_db.group(1)
                    length = re.search(r"length\s*:\s*(\d+)", options_raw)
                    if length:
                        col_info["length"] = int(length.group(1))

                    entity_info["columns"].append(col_info)

                # Parse relationships
                for otm in one_to_many.finditer(content):
                    entity_info["relationships"].append({
                        "type": "one_to_many",
                        "target_entity": otm.group(1),
                        "inverse_property": otm.group(2),
                        "property_name": otm.group(3),
                    })

                for mto in many_to_one.finditer(content):
                    rel_info: Dict[str, Any] = {
                        "type": "many_to_one",
                        "target_entity": mto.group(1),
                        "inverse_property": mto.group(2),
                        "property_name": mto.group(3),
                    }
                    # Check for @JoinColumn after this
                    after = content[mto.end():mto.end() + 200]
                    if join_column.search(after):
                        rel_info["has_join_column"] = True
                    entity_info["relationships"].append(rel_info)

                for mtm in many_to_many.finditer(content):
                    rel_info = {
                        "type": "many_to_many",
                        "target_entity": mtm.group(1),
                        "inverse_property": mtm.group(2),
                        "property_name": mtm.group(3),
                    }
                    after = content[mtm.end():mtm.end() + 200]
                    if join_table.search(after):
                        rel_info["owns_join_table"] = True
                    entity_info["relationships"].append(rel_info)

                for oto in one_to_one.finditer(content):
                    rel_info = {
                        "type": "one_to_one",
                        "target_entity": oto.group(1),
                        "inverse_property": oto.group(2),
                        "property_name": oto.group(3),
                    }
                    after = content[oto.end():oto.end() + 200]
                    if join_column.search(after):
                        rel_info["has_join_column"] = True
                    entity_info["relationships"].append(rel_info)

                entities[class_name] = entity_info

        # Also check for Prisma schema
        prisma_path = os.path.join(base, "prisma", "schema.prisma")
        if os.path.isfile(prisma_path):
            try:
                with open(prisma_path, "r", encoding="utf-8") as f:
                    prisma_content = f.read()
                prisma_entities = self._parse_prisma_schema(prisma_content)
                for name, info in prisma_entities.items():
                    if entity_name and name.lower() != entity_name.lower():
                        continue
                    if name not in entities:
                        entities[name] = info
            except Exception:
                pass

        return {
            "status": "ok",
            "entities": entities,
            "total_entities": len(entities),
        }

    def _parse_prisma_schema(self, content: str) -> Dict[str, Any]:
        """Parse Prisma schema file for models."""
        models: Dict[str, Any] = {}
        model_pattern = re.compile(r"model\s+(\w+)\s*\{([^}]+)\}", re.DOTALL)

        for mm in model_pattern.finditer(content):
            model_name = mm.group(1)
            block = mm.group(2)
            fields = []
            relationships = []

            for line in block.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("@@"):
                    continue

                field_m = re.match(
                    r"(\w+)\s+([\w\[\]?]+)\s*(.*)",
                    line,
                )
                if not field_m:
                    continue

                field_name = field_m.group(1)
                field_type = field_m.group(2)
                attrs = field_m.group(3)

                field_info: Dict[str, Any] = {
                    "name": field_name,
                    "type": field_type,
                }
                if "@id" in attrs:
                    field_info["primary_key"] = True
                if "@unique" in attrs:
                    field_info["unique"] = True
                if "@default" in attrs:
                    default_m = re.search(r"@default\(([^)]+)\)", attrs)
                    if default_m:
                        field_info["default"] = default_m.group(1)
                if "?" in field_type:
                    field_info["optional"] = True

                # Detect relations
                relation_m = re.search(r"@relation\(([^)]*)\)", attrs)
                if relation_m:
                    rel_info: Dict[str, Any] = {
                        "property_name": field_name,
                        "target_entity": field_type.rstrip("[]?"),
                        "type": "has_many" if "[]" in field_type else "belongs_to",
                    }
                    fields_m = re.search(r'fields:\s*\[([^\]]+)\]', relation_m.group(1))
                    refs_m = re.search(r'references:\s*\[([^\]]+)\]', relation_m.group(1))
                    if fields_m:
                        rel_info["fields"] = [f.strip() for f in fields_m.group(1).split(",")]
                    if refs_m:
                        rel_info["references"] = [r.strip() for r in refs_m.group(1).split(",")]
                    relationships.append(rel_info)
                elif field_type.rstrip("?[]")[0:1].isupper() and \
                     field_type.rstrip("?[]") not in ("String", "Int", "Float", "Boolean",
                                                       "DateTime", "Json", "Bytes", "BigInt", "Decimal"):
                    relationships.append({
                        "property_name": field_name,
                        "target_entity": field_type.rstrip("[]?"),
                        "type": "has_many" if "[]" in field_type else "belongs_to",
                        "inferred": True,
                    })

                fields.append(field_info)

            models[model_name] = {
                "file": "prisma/schema.prisma",
                "source": "prisma",
                "columns": fields,
                "relationships": relationships,
            }

        return models

    # ── 4. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> guard -> interceptor -> pipe -> controller ->
        service -> repository -> entity -> response.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        flows: List[Dict[str, Any]] = []

        # Find the controller
        target_content = None
        target_file = None
        target_class = None

        for finfo in all_files:
            content = finfo["content"]
            if re.search(rf"export\s+class\s+{re.escape(entry_point)}\b", content):
                target_content = content
                target_file = finfo["rel_path"]
                target_class = entry_point
                break
            # Search by route
            if re.search(rf"@Controller\s*\([^)]*{re.escape(entry_point)}[^)]*\)", content):
                cn = re.search(r"export\s+class\s+(\w+)", content)
                if cn:
                    target_content = content
                    target_file = finfo["rel_path"]
                    target_class = cn.group(1)
                    break

        if not target_content:
            return {"status": "error", "message": f"Entry point not found: {entry_point}"}

        # Parse methods
        http_method_re = re.compile(
            r"@(Get|Post|Put|Delete|Patch)\s*\(\s*(?:'([^']*)'|\"([^\"]*)\")?[^)]*\)",
        )

        lines = target_content.split("\n")
        for i, line in enumerate(lines):
            hm = http_method_re.search(line.strip())
            if not hm:
                continue

            http_verb = hm.group(1).upper()
            route_path = hm.group(2) or hm.group(3) or ""

            # Collect decorators above this method
            guards = []
            interceptors = []
            pipes = []
            j = i + 1
            while j < min(i + 15, len(lines)):
                l = lines[j].strip()
                gm = re.search(r"@UseGuards\s*\(\s*([^)]+)\)", l)
                if gm:
                    guards.extend([g.strip() for g in gm.group(1).split(",") if g.strip()])
                im = re.search(r"@UseInterceptors\s*\(\s*([^)]+)\)", l)
                if im:
                    interceptors.extend(
                        [i_name.strip() for i_name in im.group(1).split(",") if i_name.strip()]
                    )
                pm = re.search(r"@UsePipes\s*\(\s*([^)]+)\)", l)
                if pm:
                    pipes.extend([p.strip() for p in pm.group(1).split(",") if p.strip()])

                sig = re.search(r"(?:async\s+)?(\w+)\s*\(", l)
                if sig and not l.startswith("@"):
                    method_name = sig.group(1)
                    if method and method_name.lower() != method.lower():
                        break

                    # Extract method body
                    rest_content = "\n".join(lines[j:])
                    brace_idx = rest_content.find("{")
                    if brace_idx < 0:
                        break
                    body = self._extract_brace_block_str(rest_content, brace_idx)

                    flow: Dict[str, Any] = {
                        "controller": target_class,
                        "handler": method_name,
                        "http_method": http_verb,
                        "route": route_path,
                        "file": target_file,
                        "steps": [],
                    }

                    # Guards
                    if guards:
                        flow["steps"].append({
                            "type": "guards",
                            "guards": guards,
                        })

                    # Interceptors
                    if interceptors:
                        flow["steps"].append({
                            "type": "interceptors",
                            "interceptors": interceptors,
                        })

                    # Pipes
                    if pipes:
                        flow["steps"].append({
                            "type": "pipes",
                            "pipes": pipes,
                        })

                    # Service calls via this.xxxService.method()
                    service_calls = re.findall(
                        r"this\.(\w+(?:Service|Repository|Provider))\s*\.\s*(\w+)\s*\(",
                        body,
                    )
                    for svc, svc_method in service_calls:
                        flow["steps"].append({
                            "type": "service_call",
                            "service": svc,
                            "method": svc_method,
                        })

                    # Repository / ORM calls
                    repo_calls = re.findall(
                        r"this\.(\w+Repository)\s*\.\s*(find|save|create|update|delete|remove|count)\w*\s*\(",
                        body,
                    )
                    for repo, op in repo_calls:
                        flow["steps"].append({
                            "type": "data_access",
                            "repository": repo,
                            "operation": op,
                        })

                    flows.append(flow)
                    break

                if re.match(r"(export\s+class|@Module|@Controller)", l):
                    break
                j += 1

        return {
            "status": "ok",
            "entry_point": entry_point,
            "flows": flows,
            "total_flows": len(flows),
        }

    def _extract_brace_block_str(self, content: str, start: int) -> str:
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

    # ── 5. get_middleware_chain ─────────────────────────────────────────

    def get_middleware_chain(self, route_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse NestMiddleware, app.use(), module.configure().forRoutes().
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        middleware_list: List[Dict[str, Any]] = []
        global_middleware: List[Dict[str, Any]] = []

        # Global middleware: app.use()
        for finfo in all_files:
            content = finfo["content"]
            if "main.ts" in finfo["rel_path"] or "bootstrap" in finfo["rel_path"]:
                for gm in re.finditer(r"app\.use\s*\(\s*([^)]+)\)", content):
                    global_middleware.append({
                        "middleware": gm.group(1).strip(),
                        "scope": "global",
                        "file": finfo["rel_path"],
                    })

        # Module-level middleware: configure() + forRoutes()
        for finfo in all_files:
            content = finfo["content"]
            # Find configure(consumer: MiddlewareConsumer)
            configure_m = re.search(
                r"configure\s*\(\s*consumer\s*:\s*MiddlewareConsumer\s*\)\s*\{",
                content,
            )
            if not configure_m:
                continue

            brace_start = content.find("{", configure_m.start())
            block = self._extract_brace_block_str(content, brace_start)

            # Parse consumer.apply(Middleware).forRoutes(routes)
            apply_pattern = re.compile(
                r"consumer\s*\.apply\s*\(\s*([^)]+)\)\s*\.forRoutes\s*\(\s*([^)]+)\)",
                re.DOTALL,
            )
            for ap in apply_pattern.finditer(block):
                mw_names = [m.strip() for m in ap.group(1).split(",") if m.strip()]
                routes_raw = ap.group(2).strip()

                for mw in mw_names:
                    entry: Dict[str, Any] = {
                        "middleware": mw,
                        "scope": "route-specific",
                        "file": finfo["rel_path"],
                    }
                    if routes_raw:
                        entry["routes"] = routes_raw[:100]
                    middleware_list.append(entry)

        # Custom middleware classes
        custom_middleware: List[Dict[str, Any]] = []
        for finfo in all_files:
            content = finfo["content"]
            if "implements NestMiddleware" in content or "implements NestMiddleware" in content:
                cn = re.search(r"export\s+class\s+(\w+)", content)
                if cn:
                    custom_middleware.append({
                        "name": cn.group(1),
                        "file": finfo["rel_path"],
                    })

        return {
            "status": "ok",
            "global_middleware": global_middleware,
            "route_middleware": middleware_list,
            "custom_middleware": custom_middleware,
            "total": len(global_middleware) + len(middleware_list),
        }

    # ── 6. get_guard_map ───────────────────────────────────────────────

    def get_guard_map(self, guard_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @UseGuards, @Injectable() guards implementing CanActivate.
        Map guard -> endpoint usage.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        guards: Dict[str, Any] = {}
        guard_usage: List[Dict[str, Any]] = []

        # Find guard definitions
        for finfo in all_files:
            content = finfo["content"]
            if "CanActivate" in content:
                cn = re.search(r"export\s+class\s+(\w+).*implements.*CanActivate", content)
                if cn:
                    name = cn.group(1)
                    if guard_name and name.lower() != guard_name.lower():
                        continue
                    guards[name] = {
                        "file": finfo["rel_path"],
                        "used_by": [],
                    }

        # Find guard usage
        for finfo in all_files:
            content = finfo["content"]
            for gm in re.finditer(r"@UseGuards\s*\(\s*([^)]+)\)", content):
                guard_names = [g.strip() for g in gm.group(1).split(",")]
                # Find nearby method/class
                line_num = content[:gm.start()].count("\n") + 1
                lines = content.split("\n")
                context_line = ""
                for j in range(min(line_num, len(lines)), min(line_num + 5, len(lines))):
                    if re.search(r"(?:async\s+)?(\w+)\s*\(|class\s+(\w+)", lines[j]):
                        context_line = lines[j].strip()
                        break

                for gn in guard_names:
                    usage = {
                        "guard": gn,
                        "file": finfo["rel_path"],
                        "line": line_num,
                        "context": context_line[:100],
                    }
                    guard_usage.append(usage)
                    if gn in guards:
                        guards[gn]["used_by"].append({
                            "file": finfo["rel_path"],
                            "context": context_line[:80],
                        })

        return {
            "status": "ok",
            "guards": guards,
            "usage": guard_usage,
            "total_guards": len(guards),
            "total_usage": len(guard_usage),
        }

    # ── 7. get_pipe_map ────────────────────────────────────────────────

    def get_pipe_map(self, pipe_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @UsePipes, ValidationPipe, custom pipes implementing PipeTransform.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        pipes: Dict[str, Any] = {}
        pipe_usage: List[Dict[str, Any]] = []

        # Find pipe definitions
        for finfo in all_files:
            content = finfo["content"]
            if "PipeTransform" in content:
                cn = re.search(r"export\s+class\s+(\w+).*implements.*PipeTransform", content)
                if cn:
                    name = cn.group(1)
                    if pipe_name and name.lower() != pipe_name.lower():
                        continue
                    pipes[name] = {
                        "file": finfo["rel_path"],
                        "used_by": [],
                    }

        # Find pipe usage
        for finfo in all_files:
            content = finfo["content"]
            for pm in re.finditer(r"@UsePipes\s*\(\s*([^)]+)\)", content):
                pipe_refs = [p.strip() for p in pm.group(1).split(",")]
                line_num = content[:pm.start()].count("\n") + 1
                for pn in pipe_refs:
                    usage = {
                        "pipe": pn,
                        "file": finfo["rel_path"],
                        "line": line_num,
                    }
                    pipe_usage.append(usage)
                    clean_name = re.sub(r"new\s+", "", pn).split("(")[0].strip()
                    if clean_name in pipes:
                        pipes[clean_name]["used_by"].append({
                            "file": finfo["rel_path"],
                        })

        # Global pipe (in main.ts)
        global_pipes = []
        for finfo in all_files:
            if "main.ts" in finfo["rel_path"]:
                for gp in re.finditer(r"app\.useGlobalPipes\s*\(\s*new\s+(\w+)", finfo["content"]):
                    global_pipes.append(gp.group(1))

        return {
            "status": "ok",
            "pipes": pipes,
            "usage": pipe_usage,
            "global_pipes": global_pipes,
            "total_pipes": len(pipes),
        }

    # ── 8. get_dependency_injection ────────────────────────────────────

    def get_dependency_injection(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @Injectable + constructor injection. Build DI graph across modules.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        providers: Dict[str, Any] = {}
        dependency_graph: Dict[str, List[str]] = {}

        # Find @Injectable classes
        for finfo in all_files:
            content = finfo["content"]
            if "@Injectable" not in content:
                continue

            cn = re.search(r"export\s+class\s+(\w+)", content)
            if not cn:
                continue
            class_name = cn.group(1)

            if provider_name and class_name.lower() != provider_name.lower():
                continue

            # Parse constructor dependencies
            # Use balanced parenthesis matching since constructor params
            # contain nested parens like @InjectRepository(User)
            ctor_m = None
            ctor_start = content.find("constructor")
            if ctor_start != -1:
                paren_start = content.find("(", ctor_start)
                if paren_start != -1:
                    depth = 1
                    pos = paren_start + 1
                    while pos < len(content) and depth > 0:
                        if content[pos] == "(":
                            depth += 1
                        elif content[pos] == ")":
                            depth -= 1
                        pos += 1
                    if depth == 0:
                        ctor_m = content[paren_start + 1:pos - 1]
            deps: List[Dict[str, str]] = []
            if ctor_m is not None:
                params_raw = ctor_m
                # Parse each parameter - supports @Inject, @InjectRepository,
                # @InjectDataSource, @InjectConnection, and other NestJS decorators
                for param in re.finditer(
                    r"(?:@(Inject(?:Repository|DataSource|Connection|Queue|Model)?)\s*\(\s*"
                    r"(?:forwardRef\s*\(\s*\(\)\s*=>\s*)?([^)]*?)\)?\s*\)\s*)?"
                    r"(?:private|protected|public|readonly)\s+"
                    r"(?:readonly\s+)?(\w+)\s*:\s*([\w<>,\s]+?)(?:\s*[,)])",
                    params_raw,
                ):
                    decorator_name = param.group(1)
                    inject_token = param.group(2)
                    param_name = param.group(3)
                    param_type = param.group(4).strip()
                    dep: Dict[str, str] = {
                        "name": param_name,
                        "type": param_type,
                    }
                    if inject_token:
                        token_clean = inject_token.strip("'\" \t\n")
                        dep["inject_token"] = token_clean
                    if decorator_name:
                        dep["decorator"] = f"@{decorator_name}"
                    deps.append(dep)

            providers[class_name] = {
                "file": finfo["rel_path"],
                "dependencies": deps,
                "scope": "default",
            }

            # Check scope
            scope_m = re.search(r"@Injectable\s*\(\s*\{\s*scope:\s*Scope\.(\w+)", content)
            if scope_m:
                providers[class_name]["scope"] = scope_m.group(1)

            dependency_graph[class_name] = [d["type"] for d in deps]

        # Detect circular dependencies
        circular: List[List[str]] = []
        for node in dependency_graph:
            visited: Set[str] = set()
            path: List[str] = []
            self._detect_circular(node, dependency_graph, visited, path, circular)

        return {
            "status": "ok",
            "providers": providers,
            "dependency_graph": dependency_graph,
            "circular_dependencies": circular[:10],
            "total_providers": len(providers),
        }

    def _detect_circular(self, node: str, graph: Dict[str, List[str]],
                          visited: Set[str], path: List[str],
                          circular: List[List[str]]) -> None:
        """Detect circular dependencies in DI graph."""
        if node in path:
            cycle = path[path.index(node):] + [node]
            if cycle not in circular:
                circular.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        for dep in graph.get(node, []):
            self._detect_circular(dep, graph, visited, path, circular)
        path.pop()

    # ── 9. get_validation_chain ────────────────────────────────────────

    def get_validation_chain(self, controller: str, method: str) -> Dict[str, Any]:
        """
        Trace: DTO -> class-validator decorators (@IsString/@IsEmail/@IsNotEmpty)
        -> ValidationPipe -> handler.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        result: Dict[str, Any] = {
            "controller": controller,
            "method": method,
            "steps": [],
            "dto": None,
            "validations": [],
            "warnings": [],
        }

        # Find controller and method
        for finfo in all_files:
            content = finfo["content"]
            if not re.search(rf"export\s+class\s+{re.escape(controller)}\b", content):
                continue

            # Find the method
            method_pattern = re.compile(
                rf"(?:async\s+)?{re.escape(method)}\s*\(([^)]*)\)",
                re.DOTALL,
            )
            mm = method_pattern.search(content)
            if not mm:
                result["warnings"].append(f"Method '{method}' not found in {controller}")
                break

            params_raw = mm.group(1)
            result["steps"].append({
                "type": "endpoint",
                "controller": controller,
                "method": method,
            })

            # Find @Body() parameter and its DTO type
            body_param = re.search(
                r"@Body\s*\([^)]*\)\s*(?:\w+\s*:\s*)?([\w<>]+)",
                params_raw,
            )
            if body_param:
                dto_type = body_param.group(1)
                result["dto"] = {"type": dto_type}
                result["steps"].append({
                    "type": "body_binding",
                    "dto_type": dto_type,
                })

                # Find the DTO class and parse class-validator decorators
                for df in all_files:
                    dc = df["content"]
                    dto_m = re.search(
                        rf"export\s+class\s+{re.escape(dto_type)}\b",
                        dc,
                    )
                    if not dto_m:
                        continue

                    brace_start = dc.find("{", dto_m.end())
                    dto_block = self._extract_brace_block_str(dc, brace_start)

                    # Parse class-validator decorators
                    validator_pattern = re.compile(
                        r"(@(?:Is\w+|Min|Max|MinLength|MaxLength|Matches|ValidateNested|"
                        r"ArrayMinSize|ArrayMaxSize|IsOptional|IsNotEmpty|IsEnum|"
                        r"IsArray|IsUUID|IsDate|IsNumber|IsBoolean|Length|Equals|"
                        r"IsDefined|ValidateIf|Type)\s*\([^)]*\)\s*)+\s*(\w+)\s*(?:!?\s*)?:\s*([\w\[\]<>|]+)",
                    )

                    for vp in validator_pattern.finditer(dto_block):
                        decorators_raw = vp.group(0)
                        prop_name = vp.group(2)
                        prop_type = vp.group(3)

                        rules = []
                        for dec in re.finditer(r"@(\w+)\s*\(\s*([^)]*)\)", decorators_raw):
                            rule_name = dec.group(1)
                            rule_args = dec.group(2).strip()
                            rules.append({
                                "rule": rule_name,
                                "args": rule_args[:60] if rule_args else None,
                            })

                        if rules:
                            result["validations"].append({
                                "property": prop_name,
                                "type": prop_type,
                                "rules": rules,
                            })

                    result["steps"].append({
                        "type": "dto_validation",
                        "dto_type": dto_type,
                        "dto_file": df["rel_path"],
                        "validation_count": len(result["validations"]),
                    })
                    break

            # Check if ValidationPipe is used
            has_validation_pipe = False
            for f2 in all_files:
                c2 = f2["content"]
                if "ValidationPipe" in c2:
                    has_validation_pipe = True
                    break

            if has_validation_pipe:
                result["steps"].append({
                    "type": "validation_pipe",
                    "active": True,
                })
            elif result["dto"]:
                result["warnings"].append(
                    "No ValidationPipe detected -- class-validator decorators will not execute"
                )

            break

        return {"status": "ok", **result}

    # ── 10. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check: controller route, method decorator, guards, pipes, DTOs.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        checks: List[Dict[str, Any]] = []
        overall_status = "pass"

        endpoint_map = self.get_endpoint_map()
        if endpoint_map.get("status") != "ok":
            return {"status": "error", "message": "Could not parse endpoints"}

        matched = None
        for ep in endpoint_map.get("endpoints", []):
            if ep["http_method"] == method.upper():
                ep_route = re.sub(r":[\w]+", ":id", ep["route"])
                check_url = re.sub(r":[\w]+", ":id", url)
                if ep_route == check_url or ep["route"] == url:
                    matched = ep
                    break

        if matched:
            checks.append({
                "check": "route_exists",
                "status": "pass",
                "detail": f"Route {method} {url} -> {matched['controller']}.{matched['handler']}",
            })

            if matched.get("guards"):
                checks.append({
                    "check": "guards",
                    "status": "pass",
                    "detail": f"Guards: {', '.join(matched['guards'])}",
                })
            else:
                checks.append({
                    "check": "guards",
                    "status": "info",
                    "detail": "No guards configured on this endpoint",
                })

            if matched.get("pipes"):
                checks.append({
                    "check": "pipes",
                    "status": "pass",
                    "detail": f"Pipes: {', '.join(matched['pipes'])}",
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
            "matched_endpoint": matched,
        }

    # ── 11. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Patterns: naming, modules, testing, interceptors, exception-filters.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}

        if pattern_type == "naming":
            class_names: List[str] = []
            file_names: List[str] = []
            for finfo in all_files:
                file_names.append(os.path.basename(finfo["rel_path"]))
                for cm in re.finditer(r"export\s+class\s+(\w+)", finfo["content"]):
                    class_names.append(cm.group(1))

            suffixes: Dict[str, int] = {}
            for name in class_names:
                for suffix in ["Controller", "Service", "Module", "Guard",
                               "Pipe", "Interceptor", "Dto", "Entity",
                               "Repository", "Gateway", "Filter", "Subscriber"]:
                    if name.endswith(suffix):
                        suffixes[suffix] = suffixes.get(suffix, 0) + 1

            # File naming pattern
            kebab_count = sum(1 for f in file_names if "-" in f and "." in f)
            dot_count = sum(1 for f in file_names if f.count(".") >= 2)

            conventions["findings"] = [{
                "convention": "class_suffixes",
                "detail": suffixes,
            }, {
                "convention": "file_naming",
                "kebab_case_with_dots": dot_count,
                "kebab_case": kebab_count,
                "total_files": len(file_names),
            }]

        elif pattern_type == "modules":
            module_map = self.get_module_map()
            modules_data = module_map.get("modules", {})
            conventions["findings"] = [{
                "convention": "module_organization",
                "total_modules": len(modules_data),
                "modules": {
                    name: {
                        "controllers": len(info.get("controllers", [])),
                        "providers": len(info.get("providers", [])),
                        "imports": len(info.get("imports", [])),
                        "exports": len(info.get("exports", [])),
                    }
                    for name, info in list(modules_data.items())[:20]
                },
            }]

        elif pattern_type == "testing":
            test_files = self._scan_files(base, "test") + self._scan_files(base, "src", (".spec.ts",))
            test_patterns: Dict[str, int] = {}
            for finfo in test_files:
                content = finfo["content"]
                if "describe(" in content:
                    test_patterns["jest_describe"] = test_patterns.get("jest_describe", 0) + 1
                if "it(" in content or "test(" in content:
                    test_patterns["jest_test"] = test_patterns.get("jest_test", 0) + 1
                if "mock" in content.lower() or "jest.fn" in content:
                    test_patterns["mocking"] = test_patterns.get("mocking", 0) + 1
                if "createTestingModule" in content:
                    test_patterns["nest_testing_module"] = test_patterns.get("nest_testing_module", 0) + 1
                if "supertest" in content or "request(app" in content:
                    test_patterns["e2e_test"] = test_patterns.get("e2e_test", 0) + 1

            conventions["findings"] = [{
                "convention": "testing_patterns",
                "patterns": test_patterns,
                "test_file_count": len(test_files),
            }]

        elif pattern_type == "interceptors":
            interceptors = []
            for finfo in all_files:
                content = finfo["content"]
                if "NestInterceptor" in content:
                    cn = re.search(r"export\s+class\s+(\w+)", content)
                    if cn:
                        interceptors.append({
                            "name": cn.group(1),
                            "file": finfo["rel_path"],
                        })
            conventions["findings"] = [{
                "convention": "interceptors",
                "interceptors": interceptors,
                "total": len(interceptors),
            }]

        elif pattern_type == "exception-filters":
            filters = []
            for finfo in all_files:
                content = finfo["content"]
                if "@Catch" in content or "ExceptionFilter" in content:
                    cn = re.search(r"export\s+class\s+(\w+)", content)
                    catch_m = re.search(r"@Catch\s*\(\s*([^)]*)\)", content)
                    if cn:
                        filters.append({
                            "name": cn.group(1),
                            "catches": catch_m.group(1).strip() if catch_m else "all",
                            "file": finfo["rel_path"],
                        })
            conventions["findings"] = [{
                "convention": "exception_filters",
                "filters": filters,
                "total": len(filters),
            }]

        return {"status": "ok", **conventions}

    # ── 12. get_project_snapshot ───────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview: modules, controllers, services, entities, guards, pipes,
        interceptors, DTOs.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)

        modules: List[str] = []
        controllers: List[str] = []
        services: List[str] = []
        entities: List[str] = []
        guards: List[str] = []
        pipes: List[str] = []
        interceptors: List[str] = []
        dtos: List[str] = []
        test_count = 0

        for finfo in all_files:
            content = finfo["content"]
            for cm in re.finditer(r"export\s+class\s+(\w+)", content):
                name = cm.group(1)
                if name.endswith("Module"):
                    modules.append(name)
                elif name.endswith("Controller"):
                    controllers.append(name)
                elif name.endswith("Service"):
                    services.append(name)
                elif name.endswith("Guard"):
                    guards.append(name)
                elif name.endswith("Pipe"):
                    pipes.append(name)
                elif name.endswith("Interceptor"):
                    interceptors.append(name)
                elif name.endswith("Dto") or name.endswith("DTO"):
                    dtos.append(name)

            if "@Entity" in content:
                en = re.search(r"export\s+class\s+(\w+)", content)
                if en and en.group(1) not in entities:
                    entities.append(en.group(1))

            test_count += len(re.findall(r"(?:it|test)\s*\(", content))

        # Check for Prisma
        has_prisma = os.path.isfile(os.path.join(base, "prisma", "schema.prisma"))

        # Package info
        pkg_info = {}
        pkg_path = os.path.join(base, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path, "r") as f:
                    import json
                    pkg = json.load(f)
                    pkg_info = {
                        "name": pkg.get("name"),
                        "version": pkg.get("version"),
                        "nest_version": pkg.get("dependencies", {}).get("@nestjs/core"),
                    }
            except Exception:
                pass

        endpoint_map = self.get_endpoint_map()

        return {
            "status": "ok",
            "package": pkg_info,
            "modules": sorted(modules),
            "controllers": sorted(controllers),
            "services": sorted(services),
            "entities": sorted(entities),
            "guards": sorted(guards),
            "pipes": sorted(pipes),
            "interceptors": sorted(interceptors),
            "dtos": sorted(dtos)[:30],
            "has_prisma": has_prisma,
            "endpoint_count": endpoint_map.get("total", 0),
            "test_count": test_count,
            "total_ts_files": len(all_files),
            "summary": {
                "modules": len(modules),
                "controllers": len(controllers),
                "services": len(services),
                "entities": len(entities),
                "guards": len(guards),
                "pipes": len(pipes),
                "interceptors": len(interceptors),
                "dtos": len(dtos),
            },
        }

    # ── 13. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Registry: guard, interceptor, pipe, filter, gateway, microservice,
        scheduler, queue.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
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
                    rf"export\s+class\s+(\w*{re.escape(kw)}\w*)",
                    content, re.IGNORECASE,
                ):
                    keyword_matches.append({
                        "file": finfo["rel_path"],
                        "symbol": cm.group(1),
                        "type": "class",
                    })
                    if len(keyword_matches) >= 15:
                        break

        return {
            "status": "ok",
            "query": description,
            "pattern_matches": results,
            "keyword_matches": keyword_matches[:15],
        }

    # ── 14. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """Context-aware pre-edit checks for NestJS projects."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_ts_files(base)
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
        if "controller" in file_path.lower():
            result["impact"].append({"type": "route_check", "detail": "Verify routes still resolve"})
            result["impact"].append({"type": "module_check", "detail": "Check module controllers array"})
        elif "service" in file_path.lower():
            result["impact"].append({"type": "di_check", "detail": "Verify DI in consuming modules"})
            result["impact"].append({"type": "controller_check", "detail": "Check controllers injecting this"})
        elif "entity" in file_path.lower() or "schema" in file_path.lower():
            result["impact"].append({"type": "migration_needed", "detail": "Entity change may need migration"})
            result["impact"].append({"type": "repository_check", "detail": "Check repositories using this entity"})
        elif "guard" in file_path.lower():
            result["impact"].append({"type": "auth_check", "detail": "Check all endpoints using this guard"})
        elif "module" in file_path.lower():
            result["impact"].append({"type": "module_graph_check", "detail": "Check importing modules"})

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

    # ------------------------------------------------------------------ #
    #  verify_schema / detect_transaction_risks / get_domain_rules /      #
    #  generate_test_skeleton / match_view_guards                         #
    # ------------------------------------------------------------------ #

    def verify_schema(self, table_or_model: str, columns: list) -> Dict[str, Any]:
        """
        Verify that columns/fields exist in entity or Prisma schema definitions.

        Checks TypeORM @Entity @Column decorators and/or prisma/schema.prisma.
        Returns missing columns.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        found_columns: Set[str] = set()
        source_files: List[str] = []

        # Strategy 1: Check TypeORM entity files in src/
        src_dir = os.path.join(base, "src")
        if os.path.isdir(src_dir):
            entity_pattern = re.compile(
                rf"class\s+{re.escape(table_or_model)}\s*(?:extends\s+\w+\s*)?\{{(.*?)\}}",
                re.DOTALL,
            )
            column_decorator = re.compile(
                r"@(?:Column|PrimaryColumn|PrimaryGeneratedColumn|CreateDateColumn|"
                r"UpdateDateColumn|DeleteDateColumn|VersionColumn|Generated)\s*\([^)]*\)\s*"
                r"(\w+)\s*[!?]?\s*:",
            )
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    if not fname.endswith(".ts"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                    except Exception:
                        continue

                    if "@Entity" not in content:
                        continue

                    for em in entity_pattern.finditer(content):
                        entity_body = em.group(1)
                        for cm in column_decorator.finditer(entity_body):
                            found_columns.add(cm.group(1))
                        # Also grab plain property declarations that follow decorators
                        prop_pattern = re.compile(r"@\w+\([^)]*\)\s*\n\s*(\w+)\s*[!?]?\s*:")
                        for pm in prop_pattern.finditer(entity_body):
                            found_columns.add(pm.group(1))
                        source_files.append(os.path.relpath(fpath, base))

        # Strategy 2: Check Prisma schema
        prisma_path = os.path.join(base, "prisma", "schema.prisma")
        if os.path.isfile(prisma_path):
            try:
                with open(prisma_path, "r", encoding="utf-8", errors="replace") as f:
                    prisma_content = f.read()
                model_pattern = re.compile(
                    rf"model\s+{re.escape(table_or_model)}\s*\{{([^}}]*)\}}",
                    re.DOTALL | re.IGNORECASE,
                )
                mm = model_pattern.search(prisma_content)
                if mm:
                    for line in mm.group(1).split("\n"):
                        line = line.strip()
                        if not line or line.startswith("//") or line.startswith("@@"):
                            continue
                        fm = re.match(r"(\w+)\s+", line)
                        if fm:
                            found_columns.add(fm.group(1))
                    source_files.append("prisma/schema.prisma")
            except Exception:
                pass

        found = [c for c in columns if c in found_columns]
        missing = [c for c in columns if c not in found_columns]

        return {
            "status": "ok",
            "entity": table_or_model,
            "found": found,
            "missing": missing,
            "all_schema_fields": sorted(found_columns),
            "source_files": source_files,
        }

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """
        Detect transaction / atomicity risks in a NestJS file.

        Looks for missing @Transaction decorator, QueryRunner not used
        for multi-write operations, and cache calls inside transactions.
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
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        risks: List[Dict[str, Any]] = []

        write_pattern = re.compile(
            r"\.(save|insert|update|delete|remove|softDelete|softRemove|create|createMany)\s*\("
        )
        transaction_pattern = re.compile(
            r"(@Transaction\b|queryRunner|getConnection\(\)\.transaction|"
            r"dataSource\.transaction|manager\.transaction|entityManager\.transaction)"
        )
        cache_pattern = re.compile(
            r"(cacheManager\.|@CacheKey|CACHE_MANAGER|this\.cache\.|redis\.)"
        )

        # Parse method blocks
        method_pattern = re.compile(
            r"(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\w+[^{]*)?\{",
        )

        for m_match in method_pattern.finditer(content):
            method_name = m_match.group(1)
            if method_name in ("if", "for", "while", "switch", "catch", "constructor"):
                continue

            start = m_match.end()
            brace_depth = 1
            pos = start
            while pos < len(content) and brace_depth > 0:
                if content[pos] == "{":
                    brace_depth += 1
                elif content[pos] == "}":
                    brace_depth -= 1
                pos += 1
            method_body = content[start:pos]

            write_calls = write_pattern.findall(method_body)
            has_transaction = bool(transaction_pattern.search(method_body))

            if len(write_calls) >= 2 and not has_transaction:
                line_num = content[:m_match.start()].count("\n") + 1
                risks.append({
                    "type": "missing_transaction",
                    "method": method_name,
                    "line": line_num,
                    "write_count": len(write_calls),
                    "message": f"Method '{method_name}' has {len(write_calls)} DB writes without "
                               "transaction/QueryRunner",
                    "severity": "high",
                })

            if has_transaction:
                cache_calls = cache_pattern.findall(method_body)
                if cache_calls:
                    line_num = content[:m_match.start()].count("\n") + 1
                    risks.append({
                        "type": "cache_in_transaction",
                        "method": method_name,
                        "line": line_num,
                        "message": f"Cache operations inside transaction in '{method_name}' – "
                                   "cache may be set before transaction commits",
                        "severity": "medium",
                    })

        return {
            "status": "ok",
            "file": file_path,
            "risks": risks,
            "risk_count": len(risks),
        }

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """
        Return domain-aware anti-pattern rules based on file location / type.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        rules: List[Dict[str, str]] = []
        norm = file_path.replace("\\", "/").lower()

        full_path = os.path.join(base, file_path)
        content = ""
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                pass

        # Controllers → require guards
        if "controller" in norm:
            if content and not re.search(r"@UseGuards\s*\(|@Public\s*\(", content):
                rules.append({
                    "rule": "require_guards",
                    "message": "Controller should use @UseGuards() or mark routes as @Public()",
                    "severity": "warning",
                })
            if content and not re.search(r"@ApiTags|@ApiOperation|@ApiResponse", content):
                rules.append({
                    "rule": "recommend_swagger",
                    "message": "Consider adding Swagger decorators (@ApiTags, @ApiOperation) for API documentation",
                    "severity": "info",
                })

        # Services → require @Injectable
        if "service" in norm and norm.endswith(".ts"):
            if content and not re.search(r"@Injectable\s*\(", content):
                rules.append({
                    "rule": "require_injectable",
                    "message": "Service class should have @Injectable() decorator for NestJS DI",
                    "severity": "high",
                })

        # DTOs → require class-validator decorators
        if "dto" in norm:
            if content and not re.search(r"@Is\w+|@Min|@Max|@Length|@IsNotEmpty|@IsOptional|@ValidateNested", content):
                rules.append({
                    "rule": "require_validation_decorators",
                    "message": "DTO should use class-validator decorators for input validation",
                    "severity": "warning",
                })
            if content and not re.search(r"@ApiProperty", content):
                rules.append({
                    "rule": "recommend_api_property",
                    "message": "DTO should use @ApiProperty() for Swagger schema generation",
                    "severity": "info",
                })

        # Modules → require proper exports
        if "module" in norm and norm.endswith(".ts"):
            if content and re.search(r"@Module\s*\(", content):
                if not re.search(r"exports\s*:\s*\[", content):
                    rules.append({
                        "rule": "require_module_exports",
                        "message": "Module should declare exports array for providers used by other modules",
                        "severity": "info",
                    })

        # Entity files
        if "entity" in norm and norm.endswith(".ts"):
            if content and not re.search(r"@Entity\s*\(", content):
                rules.append({
                    "rule": "require_entity_decorator",
                    "message": "Entity file should have @Entity() decorator",
                    "severity": "high",
                })

        return {
            "status": "ok",
            "file": file_path,
            "rules": rules,
            "rule_count": len(rules),
        }

    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Generate a Jest test skeleton with TestingModule for a NestJS class/method.
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
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        norm = file_path.replace("\\", "/").lower()
        rel_import = os.path.splitext(file_path)[0]

        # Extract class name
        class_match = re.search(r"class\s+(\w+)", content)
        class_name = class_match.group(1) if class_match else symbol

        # Extract injected dependencies
        deps: List[str] = []
        constructor_match = re.search(r"constructor\s*\((.*?)\)", content, re.DOTALL)
        if constructor_match:
            params = constructor_match.group(1)
            for dep_match in re.finditer(r"(?:private|protected|readonly)\s+(?:readonly\s+)?(\w+)\s*:\s*(\w+)", params):
                deps.append({"var": dep_match.group(1), "type": dep_match.group(2)})

        # Build mock providers
        mock_providers = ""
        for dep in deps:
            mock_providers += (
                f"          {{\n"
                f"            provide: {dep['type']},\n"
                f"            useValue: {{\n"
                f"              // TODO: mock {dep['type']} methods\n"
                f"            }},\n"
                f"          }},\n"
            )

        var_name = class_name[0].lower() + class_name[1:]

        if "controller" in norm:
            test_type = "controller"
            skeleton = (
                f"import {{ Test, TestingModule }} from '@nestjs/testing';\n"
                f"import {{ {class_name} }} from './{os.path.basename(rel_import)}';\n\n"
                f"describe('{class_name}', () => {{\n"
                f"  let {var_name}: {class_name};\n\n"
                f"  beforeEach(async () => {{\n"
                f"    const module: TestingModule = await Test.createTestingModule({{\n"
                f"      controllers: [{class_name}],\n"
                f"      providers: [\n{mock_providers}"
                f"      ],\n"
                f"    }}).compile();\n\n"
                f"    {var_name} = module.get<{class_name}>({class_name});\n"
                f"  }});\n\n"
                f"  it('should be defined', () => {{\n"
                f"    expect({var_name}).toBeDefined();\n"
                f"  }});\n\n"
                f"  describe('{symbol}', () => {{\n"
                f"    it('should return expected result', async () => {{\n"
                f"      // Arrange\n"
                f"      // TODO: set up test data\n\n"
                f"      // Act\n"
                f"      const result = await {var_name}.{symbol}();\n\n"
                f"      // Assert\n"
                f"      expect(result).toBeDefined();\n"
                f"    }});\n"
                f"  }});\n"
                f"}});\n"
            )
        elif "service" in norm:
            test_type = "service"
            skeleton = (
                f"import {{ Test, TestingModule }} from '@nestjs/testing';\n"
                f"import {{ {class_name} }} from './{os.path.basename(rel_import)}';\n\n"
                f"describe('{class_name}', () => {{\n"
                f"  let {var_name}: {class_name};\n\n"
                f"  beforeEach(async () => {{\n"
                f"    const module: TestingModule = await Test.createTestingModule({{\n"
                f"      providers: [\n"
                f"        {class_name},\n{mock_providers}"
                f"      ],\n"
                f"    }}).compile();\n\n"
                f"    {var_name} = module.get<{class_name}>({class_name});\n"
                f"  }});\n\n"
                f"  it('should be defined', () => {{\n"
                f"    expect({var_name}).toBeDefined();\n"
                f"  }});\n\n"
                f"  describe('{symbol}', () => {{\n"
                f"    it('should return expected result', async () => {{\n"
                f"      // Arrange\n"
                f"      // TODO: set up test data and mock dependencies\n\n"
                f"      // Act\n"
                f"      const result = await {var_name}.{symbol}();\n\n"
                f"      // Assert\n"
                f"      expect(result).toBeDefined();\n"
                f"    }});\n\n"
                f"    it('should handle errors gracefully', async () => {{\n"
                f"      // TODO: mock dependency to throw\n"
                f"      await expect({var_name}.{symbol}()).rejects.toThrow();\n"
                f"    }});\n"
                f"  }});\n"
                f"}});\n"
            )
        else:
            test_type = "unit"
            skeleton = (
                f"import {{ Test, TestingModule }} from '@nestjs/testing';\n"
                f"import {{ {class_name} }} from './{os.path.basename(rel_import)}';\n\n"
                f"describe('{class_name}', () => {{\n"
                f"  let instance: {class_name};\n\n"
                f"  beforeEach(async () => {{\n"
                f"    const module: TestingModule = await Test.createTestingModule({{\n"
                f"      providers: [{class_name}],\n"
                f"    }}).compile();\n\n"
                f"    instance = module.get<{class_name}>({class_name});\n"
                f"  }});\n\n"
                f"  describe('{symbol}', () => {{\n"
                f"    it('should work correctly', async () => {{\n"
                f"      const result = await instance.{symbol}();\n"
                f"      expect(result).toBeDefined();\n"
                f"    }});\n"
                f"  }});\n"
                f"}});\n"
            )

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "test_type": test_type,
            "test_class": class_name,
            "skeleton": skeleton,
        }

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Cross-reference NestJS guard/middleware conditions with frontend
        conditional renders.
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
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        # Extract guard/auth conditions from NestJS backend code
        guards: List[Dict[str, Any]] = []
        guard_patterns = [
            (r"@UseGuards\s*\(\s*(\w+)", "use_guards"),
            (r"@Roles\s*\(\s*['\"](\w+)['\"]", "roles"),
            (r"@SetMetadata\s*\(\s*['\"](\w+)['\"]", "metadata"),
            (r"canActivate\s*\(", "can_activate"),
            (r"request\.user\.role\s*===?\s*['\"](\w+)['\"]", "role_check"),
            (r"request\.user\.permissions.*?['\"](\w+)['\"]", "permission_check"),
        ]

        for pattern, guard_type in guard_patterns:
            for m in re.finditer(pattern, content):
                line_num = content[:m.start()].count("\n") + 1
                guards.append({
                    "type": guard_type,
                    "condition": m.group(0),
                    "value": m.group(1) if m.lastindex else None,
                    "line": line_num,
                })

        # Search frontend files for matching conditional renders
        frontend_matches: List[Dict[str, Any]] = []
        search_dirs = []
        for candidate in ["client/src", "frontend/src", "src/client", "public", "views"]:
            d = os.path.join(base, candidate)
            if os.path.isdir(d):
                search_dirs.append(d)

        frontend_patterns = [
            (r"\{user\s*&&", "user_conditional"),
            (r"\{isAuthenticated\s*&&", "auth_conditional"),
            (r"\{!user\s*&&", "no_user_conditional"),
            (r"role\s*===?\s*['\"](\w+)['\"]", "role_conditional"),
            (r"hasPermission\s*\(\s*['\"](\w+)['\"]", "permission_conditional"),
            (r"isAdmin\s*&&", "admin_conditional"),
            (r"\*ngIf\s*=\s*\"(\w+)\"", "ng_if"),
            (r"v-if\s*=\s*\"(\w+)\"", "v_if"),
        ]

        for search_dir in search_dirs:
            for root, _dirs, files in os.walk(search_dir):
                for fname in files:
                    if not re.search(r"\.(tsx|jsx|ts|js|vue|html)$", fname):
                        continue
                    comp_path = os.path.join(root, fname)
                    try:
                        with open(comp_path, "r", encoding="utf-8", errors="replace") as f:
                            comp_content = f.read()
                    except Exception:
                        continue

                    rel_comp = os.path.relpath(comp_path, base)
                    for fp, ftype in frontend_patterns:
                        for fm in re.finditer(fp, comp_content):
                            comp_line = comp_content[:fm.start()].count("\n") + 1
                            for guard in guards:
                                is_match = False
                                if guard["type"] == "roles" and ftype == "role_conditional":
                                    if guard.get("value") and fm.lastindex and guard["value"] == fm.group(1):
                                        is_match = True
                                elif guard["type"] == "use_guards" and ftype in (
                                    "auth_conditional", "user_conditional"
                                ):
                                    if "Auth" in (guard.get("value") or ""):
                                        is_match = True
                                elif guard["type"] == "permission_check" and ftype == "permission_conditional":
                                    if guard.get("value") and fm.lastindex and guard["value"] == fm.group(1):
                                        is_match = True

                                if is_match:
                                    frontend_matches.append({
                                        "frontend_file": rel_comp,
                                        "frontend_line": comp_line,
                                        "frontend_type": ftype,
                                        "frontend_condition": fm.group(0),
                                        "backend_guard": guard,
                                    })

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "backend_guards": guards,
            "frontend_matches": frontend_matches,
            "matched_count": len(frontend_matches),
            "unmatched_guards": len(guards) - len({m["backend_guard"]["line"] for m in frontend_matches}),
        }
