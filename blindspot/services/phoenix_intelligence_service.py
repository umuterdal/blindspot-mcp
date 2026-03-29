"""
Phoenix Intelligence Service - Elixir/Phoenix-specific code analysis.

Provides Ecto schema relationship mapping, router pipeline parsing,
migration schema extraction, HEEx template dependency analysis,
LiveView event mapping, context boundary analysis, plug chain resolution,
and Phoenix-specific flow tracing.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class PhoenixIntelligenceService(BaseService):
    """Phoenix/Elixir-specific code intelligence."""

    SCAN_DIRS = {
        "controllers": "lib/app_web/controllers",
        "live": "lib/app_web/live",
        "views": "lib/app_web/views",
        "templates": "lib/app_web/templates",
        "contexts": "lib/app",
        "schemas": "lib/app",
        "channels": "lib/app_web/channels",
        "migrations": "priv/repo/migrations",
        "routes": "lib/app_web",
        "tests": "test",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bIO\.inspect\b",
            "severity": "warning",
            "message": "IO.inspect in production code -- use Logger instead",
            "file_types": ["ex", "exs"],
        },
        {
            "pattern": r"\bRepo\.\w+\s*\(",
            "severity": "warning",
            "message": "Direct Repo call in controller/LiveView -- use context module instead",
            "file_types": ["ex"],
        },
        {
            "pattern": r"<<\s*\w+\s*>>",
            "severity": "info",
            "message": "Binary pattern match without size -- may match unexpected data",
            "file_types": ["ex", "exs"],
        },
    ]

    PATTERN_REGISTRY = {
        "liveview": {
            "patterns": [
                r"use\s+\w+Web\s*,\s*:live_view",
                r"def\s+mount\s*\(",
                r"def\s+handle_event\s*\(",
                r"def\s+handle_info\s*\(",
                r"def\s+handle_params\s*\(",
            ],
            "description": "Phoenix LiveView lifecycle patterns",
        },
        "channel": {
            "patterns": [
                r"use\s+\w+Web\s*,\s*:channel",
                r"def\s+join\s*\(",
                r"def\s+handle_in\s*\(",
                r"broadcast\s*\(",
            ],
            "description": "Phoenix channel patterns (real-time)",
        },
        "genserver": {
            "patterns": [
                r"use\s+GenServer",
                r"def\s+init\s*\(",
                r"def\s+handle_call\s*\(",
                r"def\s+handle_cast\s*\(",
                r"def\s+handle_info\s*\(",
            ],
            "description": "OTP GenServer patterns",
        },
        "task": {
            "patterns": [
                r"Task\.async\s*\(",
                r"Task\.start\s*\(",
                r"Task\.Supervisor\.async\s*\(",
                r"Task\.await\s*\(",
            ],
            "description": "Elixir Task (async work) patterns",
        },
        "plug": {
            "patterns": [
                r"defmodule\s+\w+\s+do\s*\n\s*@behaviour\s+Plug",
                r"def\s+init\s*\(opts\)",
                r"def\s+call\s*\(conn,\s*",
                r"plug\s+:",
            ],
            "description": "Plug middleware patterns",
        },
        "live-component": {
            "patterns": [
                r"use\s+\w+Web\s*,\s*:live_component",
                r"def\s+update\s*\(",
                r"def\s+render\s*\(assigns\)",
                r"<\.live_component",
            ],
            "description": "Phoenix LiveComponent patterns",
        },
        "form-component": {
            "patterns": [
                r"<\.simple_form",
                r"<\.form",
                r"to_form\s*\(",
                r"Phoenix\.HTML\.Form",
                r"changeset",
            ],
            "description": "Phoenix form component and changeset patterns",
        },
        "table-component": {
            "patterns": [
                r"<\.table",
                r"<\.header",
                r"<\.modal",
                r"<\.flash_group",
            ],
            "description": "Phoenix CoreComponents table/UI patterns",
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
                    extensions: tuple = (".ex", ".exs")) -> List[dict]:
        """Scan directory recursively for Elixir files."""
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in (
                "_build", "deps", "node_modules", ".git", ".elixir_ls"
            )]
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

    def _scan_heex_files(self, base: str) -> List[dict]:
        """Scan for HEEx template files."""
        results: List[dict] = []
        seen: Set[str] = set()
        for d in ["lib", "."]:
            full_dir = os.path.join(base, d)
            if not os.path.isdir(full_dir):
                continue
            for root, dirs, files in os.walk(full_dir):
                dirs[:] = [dd for dd in dirs if dd not in (
                    "_build", "deps", "node_modules", ".git"
                )]
                for fname in files:
                    if not fname.endswith((".heex", ".html.heex", ".html.leex")):
                        continue
                    fpath = os.path.join(root, fname)
                    if fpath in seen:
                        continue
                    seen.add(fpath)
                    rel_path = os.path.relpath(fpath, base)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        results.append({"path": fpath, "rel_path": rel_path, "content": content})
                    except Exception:
                        continue
        return results

    def _scan_all_elixir_files(self, base: str) -> List[dict]:
        """Scan all Elixir source files in the project."""
        all_files: List[dict] = []
        seen: Set[str] = set()

        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        extra_dirs = ["lib", "config"]
        for d in extra_dirs:
            for f in self._scan_files(base, d):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    def _detect_app_name(self, base: str) -> Optional[str]:
        """Detect the application module name from mix.exs."""
        mix_path = os.path.join(base, "mix.exs")
        if os.path.isfile(mix_path):
            try:
                with open(mix_path, "r", encoding="utf-8") as f:
                    content = f.read()
                m = re.search(r"app:\s*:(\w+)", content)
                if m:
                    return m.group(1)
            except Exception:
                pass
        return None

    def _get_adaptive_scan_dirs(self, base: str) -> Dict[str, str]:
        """Adapt scan directories based on actual app name."""
        app_name = self._detect_app_name(base)
        if not app_name:
            return self.SCAN_DIRS

        web_name = f"{app_name}_web"
        return {
            "controllers": f"lib/{web_name}/controllers",
            "live": f"lib/{web_name}/live",
            "views": f"lib/{web_name}/views",
            "templates": f"lib/{web_name}/templates",
            "contexts": f"lib/{app_name}",
            "schemas": f"lib/{app_name}",
            "channels": f"lib/{web_name}/channels",
            "migrations": "priv/repo/migrations",
            "routes": f"lib/{web_name}",
            "tests": "test",
        }

    # ── 1. get_schema_relationships ────────────────────────────────────

    def get_schema_relationships(self, schema_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Ecto schemas: has_many, belongs_to, has_one, many_to_many,
        field types, virtual fields, changeset validations.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        schemas: Dict[str, Any] = {}

        # Ecto schema definition
        schema_pattern = re.compile(
            r'schema\s+"(\w+)"\s+do\b(.*?)end',
            re.DOTALL,
        )

        # Embedded schema
        embedded_schema_pattern = re.compile(
            r'embedded_schema\s+do\b(.*?)end',
            re.DOTALL,
        )

        # Module name
        module_pattern = re.compile(r"defmodule\s+([\w.]+)\s+do")

        # Relationship patterns
        has_many_pattern = re.compile(
            r'has_many\s+:(\w+)\s*,\s*([\w.]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )
        belongs_to_pattern = re.compile(
            r'belongs_to\s+:(\w+)\s*,\s*([\w.]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )
        has_one_pattern = re.compile(
            r'has_one\s+:(\w+)\s*,\s*([\w.]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )
        many_to_many_pattern = re.compile(
            r'many_to_many\s+:(\w+)\s*,\s*([\w.]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )

        # Field patterns
        field_pattern = re.compile(
            r'field\s+:(\w+)\s*,\s*([:\w.{}|]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )
        timestamps_pattern = re.compile(r"\btimestamps\s*\(\s*\)")

        # Changeset patterns
        changeset_pattern = re.compile(
            r'def\s+(changeset|[\w_]+changeset)\s*\(([^)]*)\)\s+do\b(.*?)end',
            re.DOTALL,
        )
        validate_required = re.compile(r'validate_required\s*\(\s*\w+\s*,\s*\[([^\]]+)\]')
        validate_format = re.compile(r'validate_format\s*\(\s*\w+\s*,\s*:(\w+)')
        validate_length = re.compile(r'validate_length\s*\(\s*\w+\s*,\s*:(\w+)')
        validate_inclusion = re.compile(r'validate_inclusion\s*\(\s*\w+\s*,\s*:(\w+)')
        unique_constraint = re.compile(r'unique_constraint\s*\(\s*:(\w+)')
        cast_pattern = re.compile(r'cast\s*\(\s*\w+\s*,\s*\w+\s*,\s*\[([^\]]+)\]')

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Find module name
            mod_m = module_pattern.search(content)
            if not mod_m:
                continue
            module_name = mod_m.group(1)

            # Parse schema blocks
            for sm in schema_pattern.finditer(content):
                table_name = sm.group(1)
                block = sm.group(2)

                # Extract just the module suffix for schema_name matching
                short_name = module_name.split(".")[-1]
                if schema_name and schema_name.lower() != short_name.lower() \
                   and schema_name.lower() != module_name.lower():
                    continue

                schema_info: Dict[str, Any] = {
                    "module": module_name,
                    "table": table_name,
                    "file": rel_path,
                    "fields": [],
                    "relationships": [],
                    "has_timestamps": bool(timestamps_pattern.search(block)),
                    "changesets": [],
                }

                # Parse fields
                for fm in field_pattern.finditer(block):
                    field_info: Dict[str, Any] = {
                        "name": fm.group(1),
                        "type": fm.group(2),
                    }
                    options = fm.group(3) or ""
                    if "virtual: true" in options:
                        field_info["virtual"] = True
                    if "default:" in options:
                        default_m = re.search(r"default:\s*(\S+)", options)
                        if default_m:
                            field_info["default"] = default_m.group(1)
                    schema_info["fields"].append(field_info)

                # Parse relationships
                for hm in has_many_pattern.finditer(block):
                    rel: Dict[str, Any] = {
                        "type": "has_many",
                        "name": hm.group(1),
                        "related_schema": hm.group(2),
                    }
                    opts = hm.group(3) or ""
                    fk_m = re.search(r"foreign_key:\s*:(\w+)", opts)
                    if fk_m:
                        rel["foreign_key"] = fk_m.group(1)
                    schema_info["relationships"].append(rel)

                for bm in belongs_to_pattern.finditer(block):
                    rel = {
                        "type": "belongs_to",
                        "name": bm.group(1),
                        "related_schema": bm.group(2),
                    }
                    opts = bm.group(3) or ""
                    fk_m = re.search(r"foreign_key:\s*:(\w+)", opts)
                    if fk_m:
                        rel["foreign_key"] = fk_m.group(1)
                    schema_info["relationships"].append(rel)

                for ho in has_one_pattern.finditer(block):
                    rel = {
                        "type": "has_one",
                        "name": ho.group(1),
                        "related_schema": ho.group(2),
                    }
                    schema_info["relationships"].append(rel)

                for mm in many_to_many_pattern.finditer(block):
                    rel = {
                        "type": "many_to_many",
                        "name": mm.group(1),
                        "related_schema": mm.group(2),
                    }
                    opts = mm.group(3) or ""
                    jt_m = re.search(r'join_through:\s*"(\w+)"', opts)
                    if jt_m:
                        rel["join_through"] = jt_m.group(1)
                    schema_info["relationships"].append(rel)

                # Parse changesets
                for cs in changeset_pattern.finditer(content):
                    cs_name = cs.group(1)
                    cs_body = cs.group(3)

                    cs_info: Dict[str, Any] = {
                        "name": cs_name,
                        "validations": [],
                    }

                    # Cast fields
                    cast_m = cast_pattern.search(cs_body)
                    if cast_m:
                        cs_info["cast_fields"] = [
                            f.strip().lstrip(":") for f in cast_m.group(1).split(",")
                        ]

                    # Required fields
                    req_m = validate_required.search(cs_body)
                    if req_m:
                        cs_info["required_fields"] = [
                            f.strip().lstrip(":") for f in req_m.group(1).split(",")
                        ]

                    # Other validations
                    for vf in validate_format.finditer(cs_body):
                        cs_info["validations"].append({
                            "type": "format",
                            "field": vf.group(1),
                        })
                    for vl in validate_length.finditer(cs_body):
                        cs_info["validations"].append({
                            "type": "length",
                            "field": vl.group(1),
                        })
                    for uc in unique_constraint.finditer(cs_body):
                        cs_info["validations"].append({
                            "type": "unique_constraint",
                            "field": uc.group(1),
                        })

                    schema_info["changesets"].append(cs_info)

                schemas[short_name] = schema_info

            # Also parse embedded schemas
            for esm in embedded_schema_pattern.finditer(content):
                short_name = module_name.split(".")[-1]
                if schema_name and schema_name.lower() != short_name.lower():
                    continue

                block = esm.group(1)
                fields = []
                for fm in field_pattern.finditer(block):
                    fields.append({
                        "name": fm.group(1),
                        "type": fm.group(2),
                    })

                schemas[short_name] = {
                    "module": module_name,
                    "embedded": True,
                    "file": rel_path,
                    "fields": fields,
                    "relationships": [],
                    "changesets": [],
                }

        return {
            "status": "ok",
            "schemas": schemas,
            "total_schemas": len(schemas),
        }

    # ── 2. get_route_map ───────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse router.ex: pipe_through, scope, get/post/put/delete, resources,
        live routes (live "/path", LiveView).
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        routes: List[Dict[str, Any]] = []
        pipelines: Dict[str, List[str]] = {}

        # Pipeline definition
        pipeline_pattern = re.compile(
            r'pipeline\s+:(\w+)\s+do\b(.*?)end',
            re.DOTALL,
        )

        # Scope block
        scope_pattern = re.compile(
            r'scope\s+"([^"]+)"(?:\s*,\s*([\w.]+))?\s+do\b',
        )

        # Route definitions
        route_pattern = re.compile(
            r'(get|post|put|patch|delete|head|options)\s+"([^"]+)"\s*,\s*([\w.]+)\s*,\s*:(\w+)',
        )

        # Resources
        resources_pattern = re.compile(
            r'resources\s+"([^"]+)"\s*,\s*([\w.]+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )

        # LiveView routes
        live_pattern = re.compile(
            r'live\s+"([^"]+)"\s*,\s*([\w.]+)(?:\s*,\s*:(\w+))?',
        )

        # pipe_through
        pipe_through_pattern = re.compile(
            r'pipe_through\s+(?::(\w+)|\[([^\]]+)\])',
        )

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Skip non-router files
            if "router" not in rel_path.lower() and "Router" not in content:
                continue

            # Parse pipelines
            for pp in pipeline_pattern.finditer(content):
                pipeline_name = pp.group(1)
                pipeline_body = pp.group(2)
                plugs = []
                for plug_m in re.finditer(r"plug\s+:?(\w+(?:\.\w+)*)", pipeline_body):
                    plugs.append(plug_m.group(1))
                pipelines[pipeline_name] = plugs

            # Parse routes with scope tracking
            current_scope = ""
            current_pipeline = []
            lines = content.split("\n")

            for i, line in enumerate(lines):
                stripped = line.strip()

                # Track scope
                scope_m = scope_pattern.search(stripped)
                if scope_m:
                    current_scope = scope_m.group(1)

                # Track pipe_through
                pt_m = pipe_through_pattern.search(stripped)
                if pt_m:
                    if pt_m.group(1):
                        current_pipeline = [pt_m.group(1)]
                    elif pt_m.group(2):
                        current_pipeline = [
                            p.strip().lstrip(":")
                            for p in pt_m.group(2).split(",")
                        ]

                # Regular routes
                rt_m = route_pattern.search(stripped)
                if rt_m:
                    method = rt_m.group(1).upper()
                    path = current_scope + rt_m.group(2)
                    controller = rt_m.group(3)
                    action = rt_m.group(4)

                    if filter_prefix and not path.startswith(filter_prefix):
                        continue

                    routes.append({
                        "route": path,
                        "http_method": method,
                        "controller": controller,
                        "action": action,
                        "pipeline": list(current_pipeline),
                        "file": rel_path,
                    })

                # Resources
                res_m = resources_pattern.search(stripped)
                if res_m:
                    res_path = current_scope + res_m.group(1)
                    controller = res_m.group(2)
                    options = res_m.group(3) or ""

                    # Default RESTful actions
                    actions = ["index", "new", "create", "show", "edit", "update", "delete"]
                    only_m = re.search(r'only:\s*\[([^\]]+)\]', options)
                    except_m = re.search(r'except:\s*\[([^\]]+)\]', options)
                    if only_m:
                        actions = [a.strip().lstrip(":") for a in only_m.group(1).split(",")]
                    elif except_m:
                        excluded = [a.strip().lstrip(":") for a in except_m.group(1).split(",")]
                        actions = [a for a in actions if a not in excluded]

                    method_map = {
                        "index": "GET", "show": "GET", "new": "GET", "edit": "GET",
                        "create": "POST", "update": "PUT", "delete": "DELETE",
                    }
                    for action in actions:
                        full_path = res_path
                        if action in ("show", "edit", "update", "delete"):
                            full_path += "/:id"
                        if action == "edit":
                            full_path += "/edit"
                        if action == "new":
                            full_path += "/new"

                        if filter_prefix and not full_path.startswith(filter_prefix):
                            continue

                        routes.append({
                            "route": full_path,
                            "http_method": method_map.get(action, "GET"),
                            "controller": controller,
                            "action": action,
                            "pipeline": list(current_pipeline),
                            "file": rel_path,
                            "resource": True,
                        })

                # LiveView routes
                lv_m = live_pattern.search(stripped)
                if lv_m:
                    lv_path = current_scope + lv_m.group(1)
                    lv_module = lv_m.group(2)
                    lv_action = lv_m.group(3)

                    if filter_prefix and not lv_path.startswith(filter_prefix):
                        continue

                    routes.append({
                        "route": lv_path,
                        "http_method": "LIVE",
                        "live_view": lv_module,
                        "action": lv_action,
                        "pipeline": list(current_pipeline),
                        "file": rel_path,
                    })

        return {
            "status": "ok",
            "routes": routes,
            "pipelines": pipelines,
            "total": len(routes),
        }

    # ── 3. get_migration_schema ────────────────────────────────────────

    def get_migration_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Ecto migrations: create table, add, modify, create index, references.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        migration_files = self._scan_files(base, "priv/repo/migrations", (".exs",))
        schema: Dict[str, Any] = {}

        # Create table
        create_table_pattern = re.compile(
            r'create\s+table\s*\(\s*:(\w+)(?:\s*,\s*(.+?))?\)\s+do\b(.*?)end',
            re.DOTALL,
        )

        # Add column
        add_column_pattern = re.compile(
            r'add\s+:(\w+)\s*,\s*:?(\w+)(?:\s*,\s*(.+?))?$',
            re.MULTILINE,
        )

        # References (foreign key)
        references_pattern = re.compile(
            r'add\s+:(\w+)\s*,\s*references\s*\(\s*:(\w+)(?:\s*,\s*(.+?))?\)',
        )

        # Create index
        create_index_pattern = re.compile(
            r'create\s+(?:unique\s+)?index\s*\(\s*:(\w+)\s*,\s*\[([^\]]+)\]',
        )

        # Modify column
        modify_pattern = re.compile(
            r'modify\s+:(\w+)\s*,\s*:?(\w+)',
        )

        # Alter table
        alter_table_pattern = re.compile(
            r'alter\s+table\s*\(\s*:(\w+)\s*\)\s+do\b(.*?)end',
            re.DOTALL,
        )

        for finfo in sorted(migration_files, key=lambda x: x["rel_path"]):
            content = finfo["content"]
            rel_path = finfo["rel_path"]
            migration_name = os.path.basename(rel_path).replace(".exs", "")

            # Parse create table
            for ct in create_table_pattern.finditer(content):
                tbl = ct.group(1)
                options = ct.group(2) or ""
                block = ct.group(3)

                if table_name and table_name.lower() != tbl.lower():
                    continue

                if tbl not in schema:
                    schema[tbl] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "migration_history": [],
                    }

                # Parse primary key option
                if "primary_key: false" in options:
                    schema[tbl]["primary_key"] = False

                # Parse columns
                columns = []
                for ac in add_column_pattern.finditer(block):
                    col_name = ac.group(1)
                    col_type = ac.group(2)
                    opts_raw = ac.group(3) or ""

                    col_info: Dict[str, Any] = {
                        "name": col_name,
                        "type": col_type,
                    }
                    if "null: false" in opts_raw:
                        col_info["nullable"] = False
                    if "null: true" in opts_raw:
                        col_info["nullable"] = True
                    default_m = re.search(r"default:\s*(\S+)", opts_raw)
                    if default_m:
                        col_info["default"] = default_m.group(1)
                    size_m = re.search(r"size:\s*(\d+)", opts_raw)
                    if size_m:
                        col_info["size"] = int(size_m.group(1))

                    columns.append(col_info)

                # Parse references (foreign keys)
                for ref in references_pattern.finditer(block):
                    fk_col = ref.group(1)
                    ref_table = ref.group(2)
                    opts = ref.group(3) or ""

                    fk_info: Dict[str, Any] = {
                        "column": fk_col,
                        "references_table": ref_table,
                    }
                    if "on_delete:" in opts:
                        on_delete_m = re.search(r"on_delete:\s*:(\w+)", opts)
                        if on_delete_m:
                            fk_info["on_delete"] = on_delete_m.group(1)
                    if "type:" in opts:
                        type_m = re.search(r"type:\s*:(\w+)", opts)
                        if type_m:
                            fk_info["type"] = type_m.group(1)

                    schema[tbl]["foreign_keys"].append(fk_info)
                    columns.append({
                        "name": fk_col,
                        "type": "references",
                        "references": ref_table,
                    })

                schema[tbl]["columns"] = columns

                # Check for timestamps
                if "timestamps()" in block:
                    schema[tbl]["columns"].extend([
                        {"name": "inserted_at", "type": "naive_datetime"},
                        {"name": "updated_at", "type": "naive_datetime"},
                    ])

                schema[tbl]["migration_history"].append({
                    "migration": migration_name,
                    "operation": "create_table",
                })

            # Parse create index
            for ci in create_index_pattern.finditer(content):
                tbl = ci.group(1)
                columns_raw = ci.group(2)
                cols = [c.strip().lstrip(":") for c in columns_raw.split(",")]

                if table_name and table_name.lower() != tbl.lower():
                    continue

                if tbl not in schema:
                    schema[tbl] = {
                        "columns": [], "indexes": [],
                        "foreign_keys": [], "migration_history": [],
                    }

                is_unique = "unique" in content[max(0, ci.start() - 20):ci.start()]
                schema[tbl]["indexes"].append({
                    "columns": cols,
                    "unique": is_unique,
                })

            # Parse alter table
            for at in alter_table_pattern.finditer(content):
                tbl = at.group(1)
                block = at.group(2)

                if table_name and table_name.lower() != tbl.lower():
                    continue

                if tbl not in schema:
                    schema[tbl] = {
                        "columns": [], "indexes": [],
                        "foreign_keys": [], "migration_history": [],
                    }

                for ac in add_column_pattern.finditer(block):
                    schema[tbl]["columns"].append({
                        "name": ac.group(1),
                        "type": ac.group(2),
                        "added_in": migration_name,
                    })

                schema[tbl]["migration_history"].append({
                    "migration": migration_name,
                    "operation": "alter_table",
                })

        return {
            "status": "ok",
            "schema": schema,
            "total_tables": len(schema),
        }

    # ── 4. get_template_dependencies ───────────────────────────────────

    def get_template_dependencies(self, template_path: str) -> Dict[str, Any]:
        """
        Parse HEEx: <.component>, slot, render, live_component, ~H sigil templates.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, template_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"Template not found: {template_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read file: {e}"}

        result: Dict[str, Any] = {
            "file": template_path,
            "function_components": [],
            "live_components": [],
            "slots": [],
            "assigns_used": [],
            "inner_content": False,
            "links": [],
            "forms": [],
            "conditional_blocks": [],
            "comprehensions": [],
        }

        # Function components: <.component_name ...>
        for fc in re.finditer(r"<\.(\w+)\b", content):
            comp_name = fc.group(1)
            if comp_name not in result["function_components"]:
                result["function_components"].append(comp_name)

        # Live components: <.live_component module={MyModule} ...>
        for lc in re.finditer(r'<\.live_component\s+module=\{(\w+(?:\.\w+)*)\}', content):
            result["live_components"].append(lc.group(1))

        # Also check for Phoenix.Component.live_render
        for lr in re.finditer(r'live_render\s*\(\s*@?\w+\s*,\s*([\w.]+)', content):
            result["live_components"].append(lr.group(1))

        # Slots: <:slot_name>
        for sl in re.finditer(r"<:(\w+)\b", content):
            slot_name = sl.group(1)
            if slot_name not in result["slots"]:
                result["slots"].append(slot_name)

        # Assigns: @assign_name or assigns.name
        for av in re.finditer(r"@(\w+)\b", content):
            name = av.group(1)
            if name not in ("conn", "socket", "myself") and name not in result["assigns_used"]:
                result["assigns_used"].append(name)

        # Inner content / inner_block
        if "<%= render_slot(@inner_block)" in content or "@inner_block" in content:
            result["inner_content"] = True

        # Links: <.link navigate={} patch={} href={}>
        for lnk in re.finditer(r'<\.link\s+[^>]*(?:navigate|patch|href)=\{([^}]+)\}', content):
            result["links"].append(lnk.group(1).strip())

        # Forms: <.form>, <.simple_form>
        for fm in re.finditer(r'<\.(simple_form|form)\s+[^>]*for=\{([^}]+)\}', content):
            result["forms"].append({
                "type": fm.group(1),
                "for": fm.group(2).strip(),
            })

        # Conditional blocks: <%= if %>, :if, :for
        for cond in re.finditer(r':if=\{([^}]+)\}', content):
            result["conditional_blocks"].append(cond.group(1).strip()[:80])

        # Comprehensions: :for
        for comp in re.finditer(r':for=\{([^}]+)\}', content):
            result["comprehensions"].append(comp.group(1).strip()[:80])

        return {"status": "ok", **result}

    # ── 5. get_flow_map ────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> pipeline -> plug chain -> controller/LiveView ->
        context/assigns -> Ecto query -> template render.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        flows: List[Dict[str, Any]] = []

        # Find the target module
        target_content = None
        target_file = None
        target_module = None

        for finfo in all_files:
            content = finfo["content"]
            if re.search(rf"defmodule\s+[\w.]*{re.escape(entry_point)}\s+do", content):
                target_content = content
                target_file = finfo["rel_path"]
                mod_m = re.search(r"defmodule\s+([\w.]+)\s+do", content)
                target_module = mod_m.group(1) if mod_m else entry_point
                break

        if not target_content:
            return {"status": "error", "message": f"Module not found: {entry_point}"}

        # Detect if it's a controller or LiveView
        is_liveview = bool(re.search(r"use\s+\w+Web\s*,\s*:live_view", target_content))
        is_controller = bool(re.search(r"use\s+\w+Web\s*,\s*:controller", target_content))

        # Find functions/callbacks
        fn_pattern = re.compile(
            r"def\s+(\w+)\s*\(([^)]*)\)\s+do\b(.*?)(?=\n\s*def\s|\n\s*defp\s|\Z)",
            re.DOTALL,
        )

        for fm in fn_pattern.finditer(target_content):
            fn_name = fm.group(1)
            fn_params = fm.group(2)
            fn_body = fm.group(3)

            if method and fn_name != method:
                continue

            # Skip private helper functions unless explicitly requested
            if not method and fn_name.startswith("_"):
                continue

            flow: Dict[str, Any] = {
                "module": target_module,
                "function": fn_name,
                "type": "liveview_callback" if is_liveview else "controller_action",
                "file": target_file,
                "steps": [],
            }

            # Route info
            route_map = self.get_route_map()
            for rt in route_map.get("routes", []):
                if rt.get("controller") and entry_point in rt["controller"] and \
                   rt.get("action") == fn_name:
                    flow["steps"].append({
                        "type": "route",
                        "route": rt["route"],
                        "pipeline": rt.get("pipeline", []),
                    })
                    break
                if rt.get("live_view") and entry_point in rt.get("live_view", ""):
                    flow["steps"].append({
                        "type": "live_route",
                        "route": rt["route"],
                        "pipeline": rt.get("pipeline", []),
                    })
                    break

            # Context calls: Context.function()
            ctx_calls = re.findall(
                r"([\w.]+)\.(\w+)\s*\(",
                fn_body,
            )
            for ctx_mod, ctx_fn in ctx_calls:
                if ctx_mod[0:1].isupper() and ctx_fn[0:1].islower():
                    flow["steps"].append({
                        "type": "context_call",
                        "context": ctx_mod,
                        "function": ctx_fn,
                    })

            # Repo calls
            repo_calls = re.findall(
                r"Repo\.(all|get|get!|get_by|one|insert|update|delete|aggregate)\s*\(",
                fn_body,
            )
            if repo_calls:
                flow["steps"].append({
                    "type": "repo_operations",
                    "operations": list(set(repo_calls)),
                })

            # Ecto.Query
            if "from" in fn_body and "in" in fn_body:
                flow["steps"].append({
                    "type": "ecto_query",
                    "has_query_building": True,
                })

            # Assigns (LiveView)
            if is_liveview:
                assigns = re.findall(r"assign\s*\(\s*\w+\s*,\s*:?(\w+)", fn_body)
                if assigns:
                    flow["steps"].append({
                        "type": "assigns",
                        "keys": list(set(assigns)),
                    })

            # Template render
            render_m = re.search(r'render\s*\(\s*\w+\s*,\s*"([^"]+)"', fn_body)
            if render_m:
                flow["steps"].append({
                    "type": "render",
                    "template": render_m.group(1),
                })

            # Redirect
            redirect_m = re.search(r"redirect\s*\(\s*\w+\s*,\s*to:\s*(.+?)(?:\)|$)", fn_body)
            if redirect_m:
                flow["steps"].append({
                    "type": "redirect",
                    "to": redirect_m.group(1).strip()[:80],
                })

            if flow["steps"] or method:
                flows.append(flow)

        return {
            "status": "ok",
            "entry_point": entry_point,
            "module_type": "liveview" if is_liveview else ("controller" if is_controller else "module"),
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 6. get_pipeline_chain ──────────────────────────────────────────

    def get_pipeline_chain(self, pipeline_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse router.ex pipelines: plug sequence, auth plugs, CSRF protection.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        route_map = self.get_route_map()
        pipelines = route_map.get("pipelines", {})

        if pipeline_name:
            if pipeline_name in pipelines:
                return {
                    "status": "ok",
                    "pipeline": pipeline_name,
                    "plugs": pipelines[pipeline_name],
                }
            return {"status": "error", "message": f"Pipeline not found: {pipeline_name}"}

        # Also find custom plugs
        all_files = self._scan_all_elixir_files(base)
        custom_plugs: List[Dict[str, Any]] = []

        for finfo in all_files:
            content = finfo["content"]
            if "@behaviour Plug" in content or "def call(conn," in content:
                mod_m = re.search(r"defmodule\s+([\w.]+)\s+do", content)
                if mod_m:
                    # Check for init and call functions
                    has_init = bool(re.search(r"def\s+init\s*\(", content))
                    has_call = bool(re.search(r"def\s+call\s*\(\s*(?:%Plug\.)?conn", content, re.IGNORECASE))
                    if has_init and has_call:
                        custom_plugs.append({
                            "module": mod_m.group(1),
                            "file": finfo["rel_path"],
                        })

        return {
            "status": "ok",
            "pipelines": pipelines,
            "custom_plugs": custom_plugs,
            "total_pipelines": len(pipelines),
        }

    # ── 7. get_liveview_map ────────────────────────────────────────────

    def get_liveview_map(self, liveview_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse LiveView modules: mount, handle_event, handle_info,
        handle_params, assign. Map LiveView -> component -> event chain.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        liveviews: Dict[str, Any] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            if "use" not in content:
                continue
            if not re.search(r"use\s+\w+Web\s*,\s*:live_view", content):
                continue

            mod_m = re.search(r"defmodule\s+([\w.]+)\s+do", content)
            if not mod_m:
                continue
            module_name = mod_m.group(1)
            short_name = module_name.split(".")[-1]

            if liveview_name and liveview_name.lower() != short_name.lower() \
               and liveview_name.lower() != module_name.lower():
                continue

            lv_info: Dict[str, Any] = {
                "module": module_name,
                "file": rel_path,
                "callbacks": [],
                "events": [],
                "assigns": [],
                "components_used": [],
            }

            # Parse mount
            if re.search(r"def\s+mount\s*\(", content):
                lv_info["callbacks"].append("mount")
                mount_body = self._extract_fn_body(content, "mount")
                for assign_m in re.finditer(r"assign\s*\(\s*\w+\s*,\s*:?(\w+)", mount_body):
                    if assign_m.group(1) not in lv_info["assigns"]:
                        lv_info["assigns"].append(assign_m.group(1))

            # Parse handle_event
            for he in re.finditer(r'def\s+handle_event\s*\(\s*"(\w+)"', content):
                event_name = he.group(1)
                lv_info["events"].append(event_name)
                lv_info["callbacks"].append(f"handle_event:{event_name}")

            # Parse handle_info
            for hi in re.finditer(r"def\s+handle_info\s*\(", content):
                lv_info["callbacks"].append("handle_info")

            # Parse handle_params
            if re.search(r"def\s+handle_params\s*\(", content):
                lv_info["callbacks"].append("handle_params")

            # Parse render
            if re.search(r"def\s+render\s*\(", content):
                lv_info["callbacks"].append("render")

            # Find components used in ~H sigils or render function
            for comp in re.finditer(r"<\.(\w+)\b", content):
                comp_name = comp.group(1)
                if comp_name not in lv_info["components_used"]:
                    lv_info["components_used"].append(comp_name)

            liveviews[short_name] = lv_info

        return {
            "status": "ok",
            "liveviews": liveviews,
            "total_liveviews": len(liveviews),
        }

    def _extract_fn_body(self, content: str, fn_name: str) -> str:
        """Extract the body of a function by name."""
        pattern = re.compile(
            rf"def\s+{re.escape(fn_name)}\s*\([^)]*\)\s+do\b(.*?)(?:\n\s*(?:def|defp)\s|\Z)",
            re.DOTALL,
        )
        m = pattern.search(content)
        return m.group(1) if m else ""

    # ── 8. get_context_map ─────────────────────────────────────────────

    def get_context_map(self, context_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Phoenix contexts (bounded contexts): public functions,
        Ecto queries, changesets. Map context -> schema -> controller usage.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        contexts: Dict[str, Any] = {}

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Context modules are typically in lib/app/ but not in lib/app_web/
            if "_web/" in rel_path:
                continue

            mod_m = re.search(r"defmodule\s+([\w.]+)\s+do", content)
            if not mod_m:
                continue
            module_name = mod_m.group(1)
            short_name = module_name.split(".")[-1]

            # Skip schema modules (they have `schema` blocks)
            if re.search(r'schema\s+"', content):
                continue
            # Skip if it's a test file
            if "test" in rel_path:
                continue

            if context_name and context_name.lower() != short_name.lower():
                continue

            # Check if this looks like a context (has Repo calls or schema aliases)
            has_repo = bool(re.search(r"\bRepo\.\w+", content))
            has_alias = bool(re.search(r"alias\s+[\w.]+\.[\w.]+", content))
            if not has_repo and not has_alias:
                continue

            # Parse public functions
            public_fns: List[Dict[str, Any]] = []
            for fn_m in re.finditer(
                r"@doc\s+(\"\"\".*?\"\"\"|\"[^\"]+\")\s*\n\s*def\s+(\w+)\s*\(([^)]*)\)|"
                r"def\s+(\w+)\s*\(([^)]*)\)",
                content,
                re.DOTALL,
            ):
                fn_name = fn_m.group(2) or fn_m.group(4)
                fn_params = fn_m.group(3) or fn_m.group(5)
                doc = fn_m.group(1) or ""

                if fn_name.startswith("__"):
                    continue

                public_fns.append({
                    "name": fn_name,
                    "arity": len([p for p in fn_params.split(",") if p.strip()]) if fn_params.strip() else 0,
                    "has_doc": bool(doc),
                })

            # Find aliased schemas
            schemas_used = []
            for alias_m in re.finditer(r"alias\s+([\w.]+)", content):
                aliased = alias_m.group(1)
                schemas_used.append(aliased.split(".")[-1])

            # Find who uses this context
            users: List[str] = []
            for f2 in all_files:
                if f2["rel_path"] == rel_path:
                    continue
                if short_name in f2["content"]:
                    mod2 = re.search(r"defmodule\s+([\w.]+)\s+do", f2["content"])
                    if mod2:
                        users.append(mod2.group(1).split(".")[-1])

            contexts[short_name] = {
                "module": module_name,
                "file": rel_path,
                "public_functions": public_fns,
                "schemas_used": schemas_used,
                "used_by": users[:20],
                "total_functions": len(public_fns),
            }

        return {
            "status": "ok",
            "contexts": contexts,
            "total_contexts": len(contexts),
        }

    # ── 9. verify_endpoint ─────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check route, controller/LiveView, pipeline, auth plugs.
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
            rt_method = rt.get("http_method", "")
            if rt_method == method.upper() or (method.upper() == "GET" and rt_method == "LIVE"):
                rt_route = re.sub(r":[\w]+", ":id", rt["route"])
                check_url = re.sub(r":[\w]+", ":id", url)
                if rt_route == check_url or rt["route"] == url:
                    matched = rt
                    break

        if matched:
            handler = matched.get("controller") or matched.get("live_view", "unknown")
            action = matched.get("action", "unknown")
            checks.append({
                "check": "route_exists",
                "status": "pass",
                "detail": f"Route {method} {url} -> {handler}.{action}",
            })

            if matched.get("pipeline"):
                checks.append({
                    "check": "pipeline",
                    "status": "pass",
                    "detail": f"Pipelines: {', '.join(matched['pipeline'])}",
                })

                # Check for auth in pipeline
                pipelines = route_map.get("pipelines", {})
                has_auth = False
                for pl_name in matched["pipeline"]:
                    plugs = pipelines.get(pl_name, [])
                    if any("auth" in p.lower() for p in plugs):
                        has_auth = True
                        break

                if has_auth:
                    checks.append({
                        "check": "authentication",
                        "status": "pass",
                        "detail": "Auth plug found in pipeline",
                    })
                else:
                    checks.append({
                        "check": "authentication",
                        "status": "info",
                        "detail": "No auth plug detected in pipeline -- may be intentional",
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
        }

    # ── 10. get_project_conventions ────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Patterns: naming, context, testing, liveview, ecto.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}

        if pattern_type == "naming":
            modules = []
            functions = []
            for finfo in all_files:
                content = finfo["content"]
                for mm in re.finditer(r"defmodule\s+([\w.]+)", content):
                    modules.append(mm.group(1))
                for fm in re.finditer(r"def\s+(\w+)\s*\(", content):
                    functions.append(fm.group(1))

            # Module naming conventions
            suffixes: Dict[str, int] = {}
            for mod in modules:
                short = mod.split(".")[-1]
                for suffix in ["Controller", "View", "Live", "Channel",
                               "Socket", "Plug", "Auth", "Worker", "Supervisor"]:
                    if short.endswith(suffix):
                        suffixes[suffix] = suffixes.get(suffix, 0) + 1

            snake_fns = sum(1 for f in functions if "_" in f or f.islower())
            conventions["findings"] = [{
                "convention": "module_suffixes",
                "detail": suffixes,
                "total_modules": len(modules),
            }, {
                "convention": "snake_case_functions",
                "compliant": snake_fns,
                "total": len(functions),
            }]

        elif pattern_type == "context":
            ctx_map = self.get_context_map()
            conventions["findings"] = [{
                "convention": "context_boundaries",
                "contexts": {
                    name: {
                        "functions": info["total_functions"],
                        "schemas": len(info.get("schemas_used", [])),
                        "consumers": len(info.get("used_by", [])),
                    }
                    for name, info in ctx_map.get("contexts", {}).items()
                },
            }]

        elif pattern_type == "testing":
            test_files = self._scan_files(base, "test", (".exs",))
            test_patterns: Dict[str, int] = {}
            for finfo in test_files:
                content = finfo["content"]
                if "describe" in content:
                    test_patterns["describe_blocks"] = test_patterns.get("describe_blocks", 0) + 1
                test_patterns["test_cases"] = test_patterns.get("test_cases", 0) + \
                    len(re.findall(r'\btest\s+"', content))
                if "setup" in content:
                    test_patterns["setup_blocks"] = test_patterns.get("setup_blocks", 0) + 1
                if "Mox" in content or "mock" in content.lower():
                    test_patterns["mocking"] = test_patterns.get("mocking", 0) + 1
                if "conn" in content and "get(" in content:
                    test_patterns["controller_tests"] = test_patterns.get("controller_tests", 0) + 1
                if "live(" in content:
                    test_patterns["liveview_tests"] = test_patterns.get("liveview_tests", 0) + 1

            conventions["findings"] = [{
                "convention": "testing_patterns",
                "patterns": test_patterns,
                "test_file_count": len(test_files),
            }]

        elif pattern_type == "liveview":
            lv_map = self.get_liveview_map()
            conventions["findings"] = [{
                "convention": "liveview_patterns",
                "total_liveviews": lv_map.get("total_liveviews", 0),
                "callback_distribution": {},
            }]
            callback_counts: Dict[str, int] = {}
            for lv_name, lv_info in lv_map.get("liveviews", {}).items():
                for cb in lv_info.get("callbacks", []):
                    cb_type = cb.split(":")[0]
                    callback_counts[cb_type] = callback_counts.get(cb_type, 0) + 1
            conventions["findings"][0]["callback_distribution"] = callback_counts

        elif pattern_type == "ecto":
            schema_map = self.get_schema_relationships()
            query_patterns: Dict[str, int] = {}
            for finfo in all_files:
                content = finfo["content"]
                if "from" in content and "in" in content:
                    query_patterns["query_syntax"] = query_patterns.get("query_syntax", 0) + 1
                if "Repo.preload" in content:
                    query_patterns["preload"] = query_patterns.get("preload", 0) + 1
                if "Ecto.Multi" in content:
                    query_patterns["multi_transaction"] = query_patterns.get("multi_transaction", 0) + 1
                if "Repo.transaction" in content:
                    query_patterns["transaction"] = query_patterns.get("transaction", 0) + 1

            conventions["findings"] = [{
                "convention": "ecto_patterns",
                "total_schemas": schema_map.get("total_schemas", 0),
                "query_patterns": query_patterns,
            }]

        return {"status": "ok", **conventions}

    # ── 11. get_project_snapshot ───────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview: contexts, schemas, controllers, LiveViews, channels,
        routes, migrations, plugs.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)

        controllers: List[str] = []
        liveviews: List[str] = []
        channels: List[str] = []
        schemas: List[str] = []
        contexts: List[str] = []
        plugs: List[str] = []
        test_count = 0

        for finfo in all_files:
            content = finfo["content"]
            mod_m = re.search(r"defmodule\s+([\w.]+)\s+do", content)
            if not mod_m:
                continue
            module = mod_m.group(1)
            short = module.split(".")[-1]

            if "use" in content:
                if re.search(r"use\s+\w+Web\s*,\s*:controller", content):
                    controllers.append(short)
                elif re.search(r"use\s+\w+Web\s*,\s*:live_view", content):
                    liveviews.append(short)
                elif re.search(r"use\s+\w+Web\s*,\s*:channel", content):
                    channels.append(short)

            if re.search(r'schema\s+"', content):
                schemas.append(short)
            elif "_web" not in finfo["rel_path"] and re.search(r"Repo\.\w+", content):
                if short not in contexts:
                    contexts.append(short)

            if "@behaviour Plug" in content or "def call(conn," in content:
                plugs.append(short)

            test_count += len(re.findall(r'\btest\s+"', content))

        # Mix project info
        mix_info = {}
        mix_path = os.path.join(base, "mix.exs")
        if os.path.isfile(mix_path):
            try:
                with open(mix_path, "r") as f:
                    mix_content = f.read()
                app_m = re.search(r"app:\s*:(\w+)", mix_content)
                version_m = re.search(r'version:\s*"([^"]+)"', mix_content)
                elixir_m = re.search(r'elixir:\s*"([^"]+)"', mix_content)
                mix_info = {
                    "app": app_m.group(1) if app_m else None,
                    "version": version_m.group(1) if version_m else None,
                    "elixir": elixir_m.group(1) if elixir_m else None,
                }
            except Exception:
                pass

        route_map = self.get_route_map()

        return {
            "status": "ok",
            "mix": mix_info,
            "contexts": sorted(contexts),
            "schemas": sorted(schemas),
            "controllers": sorted(controllers),
            "liveviews": sorted(liveviews),
            "channels": sorted(channels),
            "plugs": sorted(plugs),
            "route_count": route_map.get("total", 0),
            "pipelines": list(route_map.get("pipelines", {}).keys()),
            "test_count": test_count,
            "total_elixir_files": len(all_files),
            "summary": {
                "contexts": len(contexts),
                "schemas": len(schemas),
                "controllers": len(controllers),
                "liveviews": len(liveviews),
                "channels": len(channels),
                "plugs": len(plugs),
            },
        }

    # ── 12. get_similar_patterns ───────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Registry: liveview, channel, genserver, task, plug, live-component,
        form-component, table-component.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
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
                    rf"defmodule\s+([\w.]*{re.escape(kw)}[\w.]*)\s+do",
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

    # ── 13. pre_edit_check ─────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """Context-aware pre-edit checks for Phoenix projects."""
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_elixir_files(base)
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

        # Context-aware
        if "controller" in file_path.lower():
            result["impact"].append({"type": "route_check", "detail": "Verify routes in router.ex"})
            result["impact"].append({"type": "template_check", "detail": "Check templates rendered by controller"})
        elif "live" in file_path.lower():
            result["impact"].append({"type": "route_check", "detail": "Check live route in router.ex"})
            result["impact"].append({"type": "event_check", "detail": "Check JS hooks pushing events"})
        elif "schema" in file_path.lower() or "schemas" in file_path.lower():
            result["impact"].append({"type": "migration_check", "detail": "Schema change may need migration"})
            result["impact"].append({"type": "context_check", "detail": "Check context functions using schema"})
        elif "context" in file_path.lower():
            result["impact"].append({"type": "controller_check", "detail": "Check controllers calling context"})
            result["impact"].append({"type": "liveview_check", "detail": "Check LiveViews calling context"})

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

    # ── verify_schema ─────────────────────────────────────────────────
    def verify_schema(self, entity_or_model: str, fields: list) -> Dict[str, Any]:
        """Check Ecto schema field definitions and migration create table fields."""
        base = self._project_path()
        if not base:
            return {"status": "error", "message": "No project path configured"}

        result: Dict[str, Any] = {
            "schema": entity_or_model,
            "requested_fields": fields,
            "schema_fields": [],
            "migration_fields": [],
            "missing_fields": [],
            "warnings": [],
        }

        found_fields: Set[str] = set()

        for root, _, files in os.walk(base):
            if "_build" in root or "deps" in root or ".git" in root:
                continue
            for fname in files:
                if not fname.endswith((".ex", ".exs")):
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

                # Ecto schema field definitions: field :name, :type
                schema_block_re = re.compile(
                    r'schema\s+"[^"]*"\s+do\s*\n(.*?)end',
                    re.DOTALL,
                )
                for sb in schema_block_re.finditer(content):
                    body = sb.group(1)
                    field_re = re.compile(r'field\s+:(\w+)\s*,\s*:?(\w+)')
                    for fm in field_re.finditer(body):
                        field_name, field_type = fm.group(1), fm.group(2)
                        result["schema_fields"].append({
                            "file": rel, "field": field_name, "type": field_type,
                        })
                        found_fields.add(field_name)

                    # belongs_to / has_many / has_one associations
                    assoc_re = re.compile(r'(?:belongs_to|has_many|has_one)\s+:(\w+)')
                    for am in assoc_re.finditer(body):
                        assoc_name = am.group(1)
                        result["schema_fields"].append({
                            "file": rel, "field": assoc_name, "type": "association",
                        })
                        found_fields.add(assoc_name)

                # Migration create table fields
                create_re = re.compile(
                    r'create\s+table\s*\(\s*:(\w+).*?do\s*\n(.*?)end',
                    re.DOTALL,
                )
                for cm in create_re.finditer(content):
                    table_name = cm.group(1)
                    body = cm.group(2)
                    add_re = re.compile(r'add\s+:(\w+)\s*,\s*:?(\w+)')
                    for am in add_re.finditer(body):
                        col_name, col_type = am.group(1), am.group(2)
                        result["migration_fields"].append({
                            "file": rel, "table": table_name,
                            "column": col_name, "type": col_type,
                        })
                        found_fields.add(col_name)

                # alter table / add column in migrations
                alter_re = re.compile(
                    r'alter\s+table\s*\(\s*:(\w+).*?do\s*\n(.*?)end',
                    re.DOTALL,
                )
                for am in alter_re.finditer(content):
                    table_name = am.group(1)
                    body = am.group(2)
                    add_re = re.compile(r'add\s+:(\w+)\s*,\s*:?(\w+)')
                    for fm in add_re.finditer(body):
                        col_name, col_type = fm.group(1), fm.group(2)
                        result["migration_fields"].append({
                            "file": rel, "table": table_name,
                            "column": col_name, "type": col_type,
                        })
                        found_fields.add(col_name)

        for field in fields:
            if field not in found_fields:
                result["missing_fields"].append(field)

        if result["missing_fields"]:
            result["warnings"].append(
                f"Fields not found in schema or migrations: {', '.join(result['missing_fields'])}"
            )

        return {"status": "ok", **result}

    # ── detect_transaction_risks ──────────────────────────────────────
    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """Detect missing Ecto.Multi or Repo.transaction around multiple Repo operations."""
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
            "repo_op_count": 0,
            "has_transaction": False,
            "has_multi": False,
        }

        lines = content.split("\n")

        # Count Repo operations
        repo_op_re = re.compile(r'Repo\.(?:insert|update|delete|insert_all|update_all|delete_all)\s*[!(]')
        repo_locs = [(i + 1, ln.strip()) for i, ln in enumerate(lines) if repo_op_re.search(ln)]
        result["repo_op_count"] = len(repo_locs)

        # Check for transaction wrappers
        result["has_transaction"] = bool(re.search(r'Repo\.transaction', content))
        result["has_multi"] = bool(re.search(r'Ecto\.Multi', content))

        # Multiple Repo writes without transaction
        if len(repo_locs) > 1 and not result["has_transaction"] and not result["has_multi"]:
            result["risks"].append({
                "type": "multiple_repo_ops_no_transaction",
                "severity": "error",
                "message": f"Found {len(repo_locs)} Repo write operations without Ecto.Multi or Repo.transaction",
                "locations": [{"line": loc[0], "code": loc[1]} for loc in repo_locs[:5]],
            })

        # Cache operations inside transaction
        cache_re = re.compile(r'(?:Cachex|ConCache|ETS|:ets)\.\w+')
        if result["has_transaction"] or result["has_multi"]:
            for i, line in enumerate(lines):
                if cache_re.search(line):
                    result["risks"].append({
                        "type": "cache_inside_transaction",
                        "severity": "warning",
                        "message": "Cache operation inside transaction -- cache may become inconsistent on rollback",
                        "line": i + 1,
                        "code": line.strip(),
                    })

        # Repo operations in Enum.each (not atomic)
        enum_repo_re = re.compile(r'Enum\.(?:each|map)\s*\(.*?Repo\.(?:insert|update|delete)', re.DOTALL)
        if enum_repo_re.search(content):
            if not result["has_multi"] and not result["has_transaction"]:
                result["risks"].append({
                    "type": "repo_in_enum_no_transaction",
                    "severity": "error",
                    "message": "Repo operations inside Enum.each/map without transaction -- partial failures possible",
                })

        # Missing bang (!) version -- silent failures
        quiet_repo_re = re.compile(r'Repo\.(?:insert|update|delete)\s*\(')
        bang_repo_re = re.compile(r'Repo\.(?:insert|update|delete)!\s*\(')
        quiet_count = len(quiet_repo_re.findall(content))
        bang_count = len(bang_repo_re.findall(content))
        if quiet_count > 0 and bang_count == 0:
            has_case = bool(re.search(r'case\s+Repo\.', content))
            has_with = bool(re.search(r'with\s+\{:ok', content))
            if not has_case and not has_with:
                result["risks"].append({
                    "type": "unhandled_repo_errors",
                    "severity": "warning",
                    "message": f"Found {quiet_count} Repo calls without bang (!) and no case/with error handling",
                })

        if not result["risks"]:
            result["warnings"].append("No transaction risks detected")

        return {"status": "ok", **result}

    # ── get_domain_rules ──────────────────────────────────────────────
    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """Check Phoenix domain rules: plugs, Ecto.Multi, PubSub, changeset validation."""
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

        is_controller = "controller" in rel_lower
        is_context = ("context" in rel_lower or "lib/app/" in rel_lower) and not is_controller
        is_liveview = "live" in rel_lower and ("live_view" in content or "LiveView" in content)
        is_schema = bool(re.search(r'use\s+Ecto\.Schema', content)) or bool(re.search(r'schema\s+"', content))

        if is_controller:
            result["file_type"] = "controller"
            # Controllers should have plug checks
            has_auth_plug = bool(re.search(r'plug\s+:(?:require_auth|authenticate|ensure)', content))
            has_module_plug = bool(re.search(r'plug\s+\w+Auth|\bplug\s+\w+\.Auth', content))
            if not has_auth_plug and not has_module_plug:
                result["violations"].append({
                    "rule": "controller_requires_plug_checks",
                    "severity": "warning",
                    "message": "Controller lacks authentication plug -- add a plug for auth checks",
                })

            # Controllers should not have direct Repo calls
            if re.search(r'Repo\.(?:insert|update|delete|get|all)', content):
                result["violations"].append({
                    "rule": "no_repo_in_controller",
                    "severity": "warning",
                    "message": "Direct Repo call in controller -- use context module instead",
                })

        if is_context:
            result["file_type"] = "context"
            # Contexts with multiple writes should use Ecto.Multi
            repo_writes = re.findall(r'Repo\.(?:insert|update|delete)', content)
            has_multi = bool(re.search(r'Ecto\.Multi', content))
            if len(repo_writes) > 2 and not has_multi:
                result["violations"].append({
                    "rule": "context_requires_ecto_multi",
                    "severity": "warning",
                    "message": f"Context has {len(repo_writes)} Repo write operations -- use Ecto.Multi for atomicity",
                })

        if is_liveview:
            result["file_type"] = "liveview"
            # LiveViews with PubSub subscribe should have handle_info
            has_subscribe = bool(re.search(r'PubSub\.subscribe|Phoenix\.PubSub\.subscribe', content))
            has_handle_info = bool(re.search(r'def\s+handle_info\s*\(', content))
            if has_subscribe and not has_handle_info:
                result["violations"].append({
                    "rule": "liveview_requires_handle_info",
                    "severity": "error",
                    "message": "LiveView subscribes to PubSub but has no handle_info callback -- messages will be unhandled",
                })

            # LiveViews should handle disconnections
            has_handle_disconnect = bool(re.search(r'def\s+(?:terminate|handle_info.*:DOWN)', content, re.DOTALL))
            if has_subscribe and not has_handle_disconnect:
                result["suggestions"].append(
                    "Consider adding terminate/handle_info for :DOWN to handle disconnections gracefully"
                )

        if is_schema:
            result["file_type"] = "schema"
            # Schemas should have changeset validation
            has_changeset = bool(re.search(r'def\s+changeset\s*\(', content))
            if not has_changeset:
                result["violations"].append({
                    "rule": "schema_requires_changeset",
                    "severity": "warning",
                    "message": "Schema lacks changeset function -- add changeset/2 for validation",
                })
            else:
                # Changeset should have validate_required
                has_validate = bool(re.search(r'validate_required', content))
                if not has_validate:
                    result["suggestions"].append(
                        "Changeset exists but does not call validate_required -- consider adding required field validation"
                    )

        return {"status": "ok", **result}

    # ── generate_test_skeleton ────────────────────────────────────────
    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Generate ExUnit test skeleton with setup, describe blocks, and Repo sandbox."""
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
            "test_type": "unit",
            "dependencies": [],
        }

        # Detect module name
        mod_re = re.search(r'defmodule\s+([\w.]+)', content)
        module_name = mod_re.group(1) if mod_re else "MyApp.Module"

        # Extract function signatures
        fn_re = re.compile(r'def\s+(\w+)\s*\(([^)]*)\)')
        functions = [(m.group(1), m.group(2)) for m in fn_re.finditer(content)]

        is_controller = "Controller" in module_name or "controller" in file_path.lower()
        is_liveview = "LiveView" in content or "live_view" in content
        is_context = not is_controller and not is_liveview and bool(re.search(r'Repo\.', content))
        is_schema = bool(re.search(r'use\s+Ecto\.Schema', content))

        uses_repo = bool(re.search(r'Repo\.', content))

        setup_block = ""
        if uses_repo:
            result["dependencies"].append("Ecto.Adapters.SQL.Sandbox")
            setup_block = """
  setup do
    :ok = Ecto.Adapters.SQL.Sandbox.checkout(MyApp.Repo)
    :ok
  end
"""

        if is_controller:
            result["test_type"] = "controller"
            test_fns = ""
            for fn_name, params in functions:
                if fn_name.startswith("_"):
                    continue
                test_fns += f"""
  describe "{fn_name}/2" do
    test "returns success response", %{{conn: conn}} do
      conn = get(conn, ~p"/{symbol.lower()}")
      assert html_response(conn, 200) =~ ""
    end

    test "handles invalid params", %{{conn: conn}} do
      conn = get(conn, ~p"/{symbol.lower()}", %{{"invalid" => "param"}})
      assert html_response(conn, 200)
    end
  end
"""
            result["test_code"] = f"""defmodule {module_name}Test do
  use MyAppWeb.ConnCase, async: true
{setup_block}
{test_fns}end
"""
        elif is_liveview:
            result["test_type"] = "liveview"
            result["dependencies"].append("Phoenix.LiveViewTest")
            test_fns = ""
            # Find handle_event callbacks
            events = re.findall(r'def\s+handle_event\s*\(\s*"(\w+)"', content)
            for evt in events:
                test_fns += f"""
  describe "handle_event {evt}" do
    test "processes event correctly", %{{conn: conn}} do
      {{:ok, view, _html}} = live(conn, ~p"/{symbol.lower()}")
      assert view
        |> element("button", "Submit")
        |> render_click() =~ ""
    end
  end
"""
            result["test_code"] = f"""defmodule {module_name}Test do
  use MyAppWeb.ConnCase, async: true
  import Phoenix.LiveViewTest
{setup_block}
  describe "mount" do
    test "renders page", %{{conn: conn}} do
      {{:ok, view, html}} = live(conn, ~p"/{symbol.lower()}")
      assert html =~ ""
      assert has_element?(view, "h1")
    end
  end
{test_fns}end
"""
        elif is_context:
            result["test_type"] = "context"
            test_fns = ""
            for fn_name, params in functions:
                if fn_name.startswith("_"):
                    continue
                param_count = len([p for p in params.split(",") if p.strip()]) if params.strip() else 0
                test_fns += f"""
  describe "{fn_name}/{param_count}" do
    test "returns expected result" do
      # Setup test data
      result = {module_name}.{fn_name}(/* args */)
      assert result
    end

    test "handles invalid input" do
      assert {{:error, _changeset}} = {module_name}.{fn_name}(/* invalid args */)
    end
  end
"""
            result["test_code"] = f"""defmodule {module_name}Test do
  use MyApp.DataCase, async: true

  alias {module_name}
{setup_block}
{test_fns}end
"""
        elif is_schema:
            result["test_type"] = "schema"
            result["test_code"] = f"""defmodule {module_name}Test do
  use MyApp.DataCase, async: true

  alias {module_name}
{setup_block}
  describe "changeset/2" do
    test "valid attrs produce valid changeset" do
      valid_attrs = %{{}}  # TODO: add valid attributes
      changeset = {symbol}.changeset(%{symbol}{{}}, valid_attrs)
      assert changeset.valid?
    end

    test "invalid attrs produce invalid changeset" do
      invalid_attrs = %{{}}
      changeset = {symbol}.changeset(%{symbol}{{}}, invalid_attrs)
      refute changeset.valid?
    end

    test "validates required fields" do
      changeset = {symbol}.changeset(%{symbol}{{}}, %{{}})
      assert %{{}} = errors_on(changeset)
    end
  end
end
"""
        else:
            result["test_type"] = "unit"
            test_fns = ""
            for fn_name, params in functions:
                if fn_name.startswith("_"):
                    continue
                test_fns += f"""
  describe "{fn_name}" do
    test "returns expected result" do
      result = {module_name}.{fn_name}()
      assert result
    end
  end
"""
            result["test_code"] = f"""defmodule {module_name}Test do
  use ExUnit.Case, async: true

  alias {module_name}
{setup_block}
{test_fns}end
"""

        return {"status": "ok", **result}

    # ── match_view_guards ─────────────────────────────────────────────
    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """Match Plug auth checks with HEEx/LiveView socket.assigns conditions."""
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
            "backend_guards": [],
            "template_conditions": [],
            "live_session_guards": [],
            "mismatches": [],
            "warnings": [],
        }

        # Detect plug-based auth guards
        plug_patterns = [
            (r'plug\s+:(?:require_auth|authenticate|ensure_authenticated|fetch_current_user)', "auth_plug"),
            (r'plug\s+(\w+)\.(?:Auth|Require|Ensure)\w*', "module_auth_plug"),
            (r'plug\s+:put_(?:user|session|auth)', "session_plug"),
        ]
        for pattern, plug_type in plug_patterns:
            for m in re.finditer(pattern, content):
                line_no = content[:m.start()].count("\n") + 1
                result["backend_guards"].append({
                    "type": plug_type, "line": line_no,
                    "code": m.group(0).strip(),
                })

        # Detect live_session auth
        live_session_re = re.compile(
            r'live_session\s+:(\w+)\s*,\s*on_mount:\s*\[([^\]]+)\]',
        )
        for m in live_session_re.finditer(content):
            session_name = m.group(1)
            hooks = m.group(2).strip()
            result["live_session_guards"].append({
                "session": session_name, "on_mount": hooks,
            })

        # Extract role/permission checks from plugs
        backend_roles: Set[str] = set()
        role_re = re.compile(r'(?:role|permission|admin|moderator|user_type)\s*(?:==|in)\s*[:\"](\w+)')
        for m in role_re.finditer(content):
            role = m.group(1)
            backend_roles.add(role)
            result["backend_guards"].append({
                "type": "role_check", "role": role,
            })

        # Scan HEEx templates and LiveView renders for matching conditions
        template_roles: Set[str] = set()
        scan_dirs = [base]
        for scan_dir in scan_dirs:
            for root, _, files in os.walk(scan_dir):
                if "_build" in root or "deps" in root or ".git" in root:
                    continue
                for fname in files:
                    if not fname.endswith((".heex", ".html.heex", ".ex")):
                        continue
                    tpath = os.path.join(root, fname)
                    try:
                        with open(tpath, "r", encoding="utf-8", errors="replace") as f:
                            tcontent = f.read()
                    except Exception:
                        continue

                    rel_t = os.path.relpath(tpath, base)

                    # HEEx: <%= if @current_user do %> or {#if @current_user}
                    heex_auth_re = re.compile(
                        r'(?:<%=?\s*if\s+@(?:current_user|authenticated|logged_in)|\{#if\s+@(?:current_user|authenticated))',
                    )
                    for tm in heex_auth_re.finditer(tcontent):
                        result["template_conditions"].append({
                            "file": rel_t, "type": "heex_auth_check",
                            "code": tm.group(0),
                        })

                    # socket.assigns checks in LiveView
                    assigns_re = re.compile(
                        r'socket\.assigns\.(?:current_user|role|admin|authenticated)',
                    )
                    for tm in assigns_re.finditer(tcontent):
                        result["template_conditions"].append({
                            "file": rel_t, "type": "assigns_auth_check",
                            "code": tm.group(0),
                        })

                    # Role checks in templates
                    tmpl_role_re = re.compile(
                        r'@(?:current_user|user)\.role\s*==\s*:(\w+)'
                    )
                    for tm in tmpl_role_re.finditer(tcontent):
                        role = tm.group(1)
                        template_roles.add(role)
                        result["template_conditions"].append({
                            "file": rel_t, "type": "template_role_check", "role": role,
                        })

        # Find mismatches
        for role in backend_roles:
            if role not in template_roles:
                result["mismatches"].append({
                    "type": "backend_role_no_template_guard",
                    "role": role,
                    "message": f"Backend checks role '{role}' but no matching template condition found",
                })
        for role in template_roles:
            if role not in backend_roles:
                result["mismatches"].append({
                    "type": "template_guard_no_backend_role",
                    "role": role,
                    "message": f"Template checks role '{role}' but no matching backend plug guard found",
                })

        if not result["backend_guards"] and not result["live_session_guards"]:
            result["warnings"].append("No auth plugs or live_session guards detected in this file")

        return {"status": "ok", **result}
