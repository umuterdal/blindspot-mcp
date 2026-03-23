"""
Rails Intelligence Service - Ruby on Rails-specific code analysis.

Provides ActiveRecord relationship parsing, route mapping from config/routes.rb,
migration schema extraction, ERB/HAML template dependency analysis, cache mapping,
middleware resolution, and flow tracing for Rails applications.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class RailsIntelligenceService(BaseService):
    """Rails-specific code intelligence for Ruby on Rails projects."""

    SCAN_DIRS = {
        "models": "app/models",
        "controllers": "app/controllers",
        "views": "app/views",
        "services": "app/services",
        "jobs": "app/jobs",
        "mailers": "app/mailers",
        "migrations": "db/migrate",
        "routes": "config",
        "tests": "test",
    }

    ANTI_PATTERNS = [
        {
            "pattern": r"\bputs\b\s",
            "severity": "warning",
            "message": "puts in production code -- use Rails.logger instead",
            "file_types": ["rb"],
        },
        {
            "pattern": r"\bp\b\s",
            "severity": "warning",
            "message": "p() debug output in code -- use Rails.logger instead",
            "file_types": ["rb"],
        },
        {
            "pattern": r"\.all\b(?!\s*\.\s*(?:includes|eager_load|preload|joins|where|select|order|limit))",
            "severity": "warning",
            "message": "Potential N+1 query -- consider adding .includes() or .eager_load()",
            "file_types": ["rb"],
        },
        {
            "pattern": r"\.save!",
            "severity": "info",
            "message": "save! without rescue -- consider handling ActiveRecord::RecordInvalid",
            "file_types": ["rb"],
        },
        {
            "pattern": r"binding\.pry",
            "severity": "error",
            "message": "binding.pry debugger left in code -- remove before commit",
            "file_types": ["rb"],
        },
    ]

    PATTERN_REGISTRY = {
        "service-object": {
            "patterns": [
                r"class\s+\w+Service",
                r"def\s+call\b",
                r"def\s+self\.call\b",
                r"def\s+perform\b",
            ],
            "description": "Service object pattern implementations",
        },
        "form-object": {
            "patterns": [
                r"class\s+\w+Form",
                r"include\s+ActiveModel::Model",
                r"include\s+ActiveModel::Validations",
            ],
            "description": "Form object pattern implementations",
        },
        "query-object": {
            "patterns": [
                r"class\s+\w+Query",
                r"class\s+\w+Finder",
                r"class\s+\w+Filter",
                r"\.where\s*\(",
                r"\.joins\s*\(",
            ],
            "description": "Query object / finder pattern implementations",
        },
        "presenter": {
            "patterns": [
                r"class\s+\w+Presenter",
                r"class\s+\w+Decorator",
                r"class\s+\w+ViewModel",
            ],
            "description": "Presenter / decorator pattern implementations",
        },
        "decorator": {
            "patterns": [
                r"class\s+\w+Decorator",
                r"SimpleDelegator",
                r"Draper",
                r"delegate\s+",
            ],
            "description": "Decorator pattern implementations",
        },
        "concern": {
            "patterns": [
                r"module\s+\w+",
                r"extend\s+ActiveSupport::Concern",
                r"included\s+do",
                r"class_methods\s+do",
            ],
            "description": "ActiveSupport::Concern patterns",
        },
        "mailer": {
            "patterns": [
                r"class\s+\w+Mailer\s*<\s*(?:Application|Action)Mailer",
                r"\.deliver_later",
                r"\.deliver_now",
                r"mail\s*\(",
            ],
            "description": "ActionMailer patterns",
        },
        "job": {
            "patterns": [
                r"class\s+\w+Job\s*<\s*(?:Application|Active)Job",
                r"\.perform_later",
                r"\.perform_now",
                r"def\s+perform\b",
                r"queue_as\s+",
            ],
            "description": "ActiveJob / background job patterns",
        },
        "channel": {
            "patterns": [
                r"class\s+\w+Channel\s*<\s*(?:Application|Action)Cable",
                r"def\s+subscribed",
                r"def\s+unsubscribed",
                r"stream_from",
                r"stream_for",
            ],
            "description": "ActionCable channel patterns",
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

    def _scan_files(self, base: str, directory: str, extensions: tuple = (".rb",)) -> List[dict]:
        """Scan directory recursively for Ruby files."""
        results = []
        full_dir = os.path.join(base, directory)
        if not os.path.isdir(full_dir):
            return results

        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "vendor", "tmp")]
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

    def _scan_all_source(self, base: str, extensions: tuple = (".rb",)) -> List[dict]:
        """Scan all known source directories."""
        all_files = []
        seen = set()
        for category, directory in self.SCAN_DIRS.items():
            for f in self._scan_files(base, directory, extensions):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        # Also scan lib/ and config/
        for extra_dir in ["lib", "config", "app"]:
            for f in self._scan_files(base, extra_dir, extensions):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    all_files.append(f)

        return all_files

    # ── 1. get_model_relationships ────────────────────────────────────

    def get_model_relationships(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse ActiveRecord relationships, validations, scopes, enums, and callbacks.

        Args:
            model_name: Optional model name (e.g., "User"). If omitted, returns all.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        model_files = self._scan_files(base, self.SCAN_DIRS["models"])
        if not model_files:
            return {"status": "error", "message": f"Models directory not found at {self.SCAN_DIRS['models']}"}

        all_models: Dict[str, Any] = {}

        for finfo in model_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Extract class name
            class_match = re.search(
                r"class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base|(\w+))",
                content,
            )
            if not class_match:
                continue

            name = class_match.group(1)
            parent = class_match.group(2) or "ApplicationRecord"

            if model_name and name.lower() != model_name.lower():
                continue

            model_info: Dict[str, Any] = {
                "file": rel_path,
                "parent": parent,
                "relationships": [],
                "validations": [],
                "scopes": [],
                "enums": [],
                "callbacks": [],
                "concerns": [],
            }

            # Relationships: has_many, belongs_to, has_one, has_and_belongs_to_many
            rel_pattern = re.compile(
                r"(has_many|belongs_to|has_one|has_and_belongs_to_many)\s+"
                r":(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
                re.MULTILINE,
            )
            for m in rel_pattern.finditer(content):
                rel_info: Dict[str, Any] = {
                    "type": m.group(1),
                    "name": m.group(2),
                }
                options_str = m.group(3) or ""

                # Parse through:
                through_match = re.search(r"through:\s*:(\w+)", options_str)
                if through_match:
                    rel_info["through"] = through_match.group(1)

                # Parse class_name:
                class_name_match = re.search(r"class_name:\s*['\"](\w+)['\"]", options_str)
                if class_name_match:
                    rel_info["class_name"] = class_name_match.group(1)

                # Parse foreign_key:
                fk_match = re.search(r"foreign_key:\s*:?['\"]?(\w+)", options_str)
                if fk_match:
                    rel_info["foreign_key"] = fk_match.group(1)

                # Parse dependent:
                dep_match = re.search(r"dependent:\s*:(\w+)", options_str)
                if dep_match:
                    rel_info["dependent"] = dep_match.group(1)

                # Parse inverse_of:
                inv_match = re.search(r"inverse_of:\s*:(\w+)", options_str)
                if inv_match:
                    rel_info["inverse_of"] = inv_match.group(1)

                model_info["relationships"].append(rel_info)

            # Validations: validates :field, presence: true, etc.
            val_pattern = re.compile(
                r"validates?\s+:(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
                re.MULTILINE,
            )
            for m in val_pattern.finditer(content):
                val_info: Dict[str, Any] = {
                    "field": m.group(1),
                }
                options_str = m.group(2) or ""

                # Extract validation types
                val_types = re.findall(r"(\w+):\s*(?:true|\{[^}]*\}|[^,\n]+)", options_str)
                if val_types:
                    val_info["validations"] = val_types

                model_info["validations"].append(val_info)

            # Scopes: scope :active, -> { where(active: true) }
            scope_pattern = re.compile(
                r"scope\s+:(\w+)\s*,\s*->\s*(?:\([^)]*\))?\s*\{(.+?)\}",
                re.DOTALL,
            )
            for m in scope_pattern.finditer(content):
                model_info["scopes"].append({
                    "name": m.group(1),
                    "body": m.group(2).strip()[:100],
                })

            # Enums: enum status: { active: 0, inactive: 1 }
            enum_pattern = re.compile(
                r"enum\s+(\w+):\s*(?:\{([^}]+)\}|\[([^\]]+)\])",
            )
            for m in enum_pattern.finditer(content):
                values_str = m.group(2) or m.group(3) or ""
                values = re.findall(r":?(\w+)", values_str)
                model_info["enums"].append({
                    "name": m.group(1),
                    "values": values,
                })

            # Callbacks: before_save, after_save, before_create, etc.
            callback_pattern = re.compile(
                r"(before_save|after_save|before_create|after_create|before_update|after_update|"
                r"before_destroy|after_destroy|before_validation|after_validation|"
                r"after_commit|after_rollback|after_initialize|after_find)\s+:(\w+)",
            )
            for m in callback_pattern.finditer(content):
                model_info["callbacks"].append({
                    "type": m.group(1),
                    "method": m.group(2),
                })

            # Concerns: include Searchable, include Trackable
            concern_pattern = re.compile(
                r"include\s+(\w+(?:::\w+)*)",
            )
            for m in concern_pattern.finditer(content):
                concern = m.group(1)
                if concern not in ("ActiveModel", "ActiveRecord"):
                    model_info["concerns"].append(concern)

            all_models[name] = model_info

        if model_name and not all_models:
            return {"status": "error", "message": f"Model '{model_name}' not found"}

        return {
            "status": "success",
            "models": all_models,
            "total_models": len(all_models),
        }

    # ── 2. get_route_map ──────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse config/routes.rb to extract route -> controller#action mapping.

        Supports: resources, get/post/put/delete, namespace, scope, mount.

        Args:
            filter_prefix: Optional URL prefix filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes_file = os.path.join(base, "config", "routes.rb")
        if not os.path.isfile(routes_file):
            return {"status": "error", "message": "config/routes.rb not found"}

        try:
            with open(routes_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read routes file: {e}"}

        routes: List[Dict[str, Any]] = []

        # Parse the routes file recursively with namespace/scope tracking
        self._parse_rails_routes(content, routes, prefix="", namespace="", constraints=None)

        # Apply filter
        if filter_prefix:
            routes = [r for r in routes if r.get("path", "").startswith(filter_prefix)]

        total = len(routes)
        if not filter_prefix and total > 60:
            prefixes = sorted(set(
                "/" + r["path"].strip("/").split("/")[0]
                for r in routes if r.get("path") and r["path"] != "/"
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

    def _parse_rails_routes(self, content: str, routes: List[Dict], prefix: str,
                             namespace: str, constraints: Optional[str]) -> None:
        """Recursively parse Rails route definitions."""

        # Direct route methods: get '/path', to: 'controller#action'
        direct_pattern = re.compile(
            r"(get|post|put|patch|delete)\s+['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*to:\s*['\"]([^'\"]+)['\"])?",
            re.MULTILINE,
        )
        for m in direct_pattern.finditer(content):
            method = m.group(1).upper()
            path = m.group(2)
            controller_action = m.group(3) or ""

            full_path = prefix + ("/" + path.lstrip("/") if path else "")
            controller = ""
            action = ""
            if "#" in controller_action:
                parts = controller_action.split("#")
                controller = parts[0]
                action = parts[1] if len(parts) > 1 else ""
                if namespace:
                    controller = f"{namespace}/{controller}"

            routes.append({
                "method": method,
                "path": full_path,
                "controller": controller,
                "action": action,
            })

        # root route: root 'controller#action' or root to: 'controller#action'
        root_pattern = re.compile(
            r"root\s+(?:to:\s*)?['\"]([^'\"]+)['\"]",
        )
        for m in root_pattern.finditer(content):
            controller_action = m.group(1)
            controller = ""
            action = ""
            if "#" in controller_action:
                parts = controller_action.split("#")
                controller = parts[0]
                action = parts[1] if len(parts) > 1 else ""
            routes.append({
                "method": "GET",
                "path": prefix + "/",
                "controller": controller,
                "action": action,
                "root": True,
            })

        # resources :users
        resources_pattern = re.compile(
            r"resources?\s+:(\w+)(?:\s*,\s*(.+?))?(?:\s+do\b)?",
            re.MULTILINE,
        )
        for m in resources_pattern.finditer(content):
            resource_name = m.group(1)
            options_str = m.group(2) or ""
            is_singular = m.group(0).strip().startswith("resource ")

            resource_path = prefix + "/" + resource_name
            controller = (namespace + "/" if namespace else "") + resource_name

            # Parse only: and except:
            only_match = re.search(r"only:\s*\[([^\]]*)\]", options_str)
            except_match = re.search(r"except:\s*\[([^\]]*)\]", options_str)

            all_actions = ["index", "show", "new", "create", "edit", "update", "destroy"]
            if is_singular:
                all_actions = ["show", "new", "create", "edit", "update", "destroy"]

            if only_match:
                allowed = re.findall(r":(\w+)", only_match.group(1))
                actions = [a for a in all_actions if a in allowed]
            elif except_match:
                excluded = re.findall(r":(\w+)", except_match.group(1))
                actions = [a for a in all_actions if a not in excluded]
            else:
                actions = all_actions

            # Generate RESTful routes
            action_map = {
                "index": ("GET", resource_path),
                "new": ("GET", resource_path + "/new"),
                "create": ("POST", resource_path),
                "show": ("GET", resource_path + "/:id"),
                "edit": ("GET", resource_path + "/:id/edit"),
                "update": ("PATCH", resource_path + "/:id"),
                "destroy": ("DELETE", resource_path + "/:id"),
            }

            for action in actions:
                if action in action_map:
                    method, path = action_map[action]
                    routes.append({
                        "method": method,
                        "path": path,
                        "controller": controller,
                        "action": action,
                    })

        # namespace :admin do ... end
        ns_pattern = re.compile(
            r"namespace\s+:(\w+)\s+do\b(.*?)(?:^  end|\nend)",
            re.DOTALL | re.MULTILINE,
        )
        for m in ns_pattern.finditer(content):
            ns_name = m.group(1)
            ns_body = m.group(2)
            new_prefix = prefix + "/" + ns_name
            new_namespace = (namespace + "/" if namespace else "") + ns_name
            self._parse_rails_routes(ns_body, routes, new_prefix, new_namespace, constraints)

        # scope '/api' do ... end
        scope_pattern = re.compile(
            r"scope\s+['\"]([^'\"]+)['\"]\s+do\b(.*?)(?:^  end|\nend)",
            re.DOTALL | re.MULTILINE,
        )
        for m in scope_pattern.finditer(content):
            scope_path = m.group(1)
            scope_body = m.group(2)
            new_prefix = prefix + scope_path
            self._parse_rails_routes(scope_body, routes, new_prefix, namespace, constraints)

        # mount Engine, at: '/path'
        mount_pattern = re.compile(
            r"mount\s+(\w+(?:::\w+)*)\s*,\s*at:\s*['\"]([^'\"]+)['\"]",
        )
        for m in mount_pattern.finditer(content):
            routes.append({
                "method": "MOUNT",
                "path": prefix + m.group(2),
                "engine": m.group(1),
                "controller": m.group(1),
                "action": "mount",
            })

    # ── 3. get_migration_schema ───────────────────────────────────────

    def get_migration_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse db/migrate/*.rb migration files to extract schema information.

        Args:
            table_name: Optional table name filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        migrations_dir = os.path.join(base, self.SCAN_DIRS["migrations"])
        if not os.path.isdir(migrations_dir):
            return {"status": "error", "message": f"Migrations directory not found at {self.SCAN_DIRS['migrations']}"}

        tables: Dict[str, Any] = {}
        migration_files = self._scan_files(base, self.SCAN_DIRS["migrations"])

        for finfo in sorted(migration_files, key=lambda x: x["rel_path"]):
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # create_table
            create_pattern = re.compile(
                r"create_table\s+:(\w+)(?:\s*,\s*(.+?))?\s+do\s*\|(\w+)\|",
                re.DOTALL,
            )
            for m in create_pattern.finditer(content):
                tname = m.group(1)
                options_str = m.group(2) or ""
                table_var = m.group(3)

                if table_name and tname.lower() != table_name.lower():
                    continue

                # Find the block content
                block_start = content.find(f"do |{table_var}|", m.start())
                if block_start == -1:
                    continue

                # Find the end of the block (naive: look for 'end')
                block_content = self._extract_ruby_block(content, block_start)

                columns = self._parse_rails_columns(block_content, table_var)
                indexes = self._parse_rails_indexes(block_content, table_var)
                references = self._parse_rails_references(block_content, table_var)

                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                tables[tname]["columns"].extend(columns)
                tables[tname]["indexes"].extend(indexes)
                tables[tname]["foreign_keys"].extend(references)
                tables[tname]["files"].append(rel_path)

                # Check for id: false option
                if "id: false" in options_str:
                    tables[tname]["id"] = False

            # add_column :table, :column, :type
            add_col_pattern = re.compile(
                r"add_column\s+:(\w+)\s*,\s*:(\w+)\s*,\s*:(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
            )
            for m in add_col_pattern.finditer(content):
                tname = m.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue
                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                col_info: Dict[str, Any] = {
                    "name": m.group(2),
                    "type": m.group(3),
                    "operation": "add_column",
                    "migration_file": rel_path,
                }

                options = m.group(4) or ""
                if "null: false" in options:
                    col_info["nullable"] = False
                default_match = re.search(r"default:\s*([^,\n]+)", options)
                if default_match:
                    col_info["default"] = default_match.group(1).strip()

                tables[tname]["columns"].append(col_info)
                if rel_path not in tables[tname]["files"]:
                    tables[tname]["files"].append(rel_path)

            # add_index :table, [:col1, :col2]
            add_idx_pattern = re.compile(
                r"add_index\s+:(\w+)\s*,\s*(?::(\w+)|\[([^\]]+)\])(?:\s*,\s*(.+?))?(?:\n|\Z)",
            )
            for m in add_idx_pattern.finditer(content):
                tname = m.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue
                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                cols_single = m.group(2)
                cols_array = m.group(3)
                options = m.group(4) or ""

                if cols_single:
                    idx_cols = [cols_single]
                else:
                    idx_cols = re.findall(r":(\w+)", cols_array or "")

                idx_info: Dict[str, Any] = {"columns": idx_cols, "migration_file": rel_path}
                if "unique: true" in options:
                    idx_info["unique"] = True

                tables[tname]["indexes"].append(idx_info)

            # add_reference :table, :other_model
            add_ref_pattern = re.compile(
                r"add_reference\s+:(\w+)\s*,\s*:(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
            )
            for m in add_ref_pattern.finditer(content):
                tname = m.group(1)
                if table_name and tname.lower() != table_name.lower():
                    continue
                if tname not in tables:
                    tables[tname] = {"columns": [], "indexes": [], "foreign_keys": [], "files": []}

                ref_info: Dict[str, Any] = {
                    "reference": m.group(2),
                    "migration_file": rel_path,
                }
                options = m.group(3) or ""
                if "foreign_key: true" in options:
                    ref_info["foreign_key"] = True

                tables[tname]["foreign_keys"].append(ref_info)

        if table_name and not tables:
            return {"status": "error", "message": f"Table '{table_name}' not found in migrations"}

        return {
            "status": "success",
            "tables": tables,
            "total_tables": len(tables),
        }

    def _extract_ruby_block(self, content: str, start: int) -> str:
        """Extract a Ruby do...end block starting at 'start'."""
        # Find 'do' keyword after start
        do_pos = content.find("do", start)
        if do_pos == -1:
            return ""

        # Count do/end nesting
        search_content = content[do_pos:]
        depth = 0
        pos = 0
        while pos < len(search_content):
            # Look for 'do' and 'end' as whole words
            if search_content[pos:pos + 2] == "do" and (pos + 2 >= len(search_content) or not search_content[pos + 2].isalnum()):
                if pos == 0 or not search_content[pos - 1].isalnum():
                    depth += 1
            elif search_content[pos:pos + 3] == "end" and (pos + 3 >= len(search_content) or not search_content[pos + 3].isalnum()):
                if pos == 0 or not search_content[pos - 1].isalnum():
                    depth -= 1
                    if depth == 0:
                        return search_content[2:pos].strip()
            pos += 1

        return search_content[2:].strip()

    def _parse_rails_columns(self, block: str, table_var: str) -> List[Dict[str, Any]]:
        """Parse column definitions from a Rails migration block."""
        columns = []
        # t.string :name, null: false, default: "value"
        col_pattern = re.compile(
            rf"{re.escape(table_var)}\.(string|integer|bigint|text|boolean|date|datetime|timestamp|"
            r"float|decimal|binary|json|jsonb|uuid|references|timestamps|inet|cidr|macaddr|"
            r"hstore|array|numrange|tsrange|daterange|int4range|int8range|point|line|polygon|"
            r"circle|box|path|bit|bit_varying)\s*(?::(\w+))?\s*(?:,\s*(.+?))?(?:\n|\Z)",
        )
        for m in col_pattern.finditer(block):
            col_type = m.group(1)
            col_name = m.group(2)
            options = m.group(3) or ""

            if col_type == "timestamps":
                columns.append({"name": "created_at", "type": "datetime"})
                columns.append({"name": "updated_at", "type": "datetime"})
                continue

            if not col_name:
                continue

            col_info: Dict[str, Any] = {"name": col_name, "type": col_type}

            if "null: false" in options:
                col_info["nullable"] = False
            default_match = re.search(r"default:\s*([^,\n]+)", options)
            if default_match:
                col_info["default"] = default_match.group(1).strip()
            if "index: true" in options:
                col_info["indexed"] = True
            if "unique: true" in options:
                col_info["unique"] = True

            columns.append(col_info)

        return columns

    def _parse_rails_indexes(self, block: str, table_var: str) -> List[Dict[str, Any]]:
        """Parse index definitions from a Rails migration block."""
        indexes = []
        idx_pattern = re.compile(
            rf"{re.escape(table_var)}\.index\s+(?::(\w+)|\[([^\]]+)\])(?:\s*,\s*(.+?))?(?:\n|\Z)",
        )
        for m in idx_pattern.finditer(block):
            single = m.group(1)
            array = m.group(2)
            options = m.group(3) or ""

            cols = [single] if single else re.findall(r":(\w+)", array or "")
            idx_info: Dict[str, Any] = {"columns": cols}
            if "unique: true" in options:
                idx_info["unique"] = True
            indexes.append(idx_info)

        return indexes

    def _parse_rails_references(self, block: str, table_var: str) -> List[Dict[str, Any]]:
        """Parse reference/foreign key definitions."""
        refs = []
        ref_pattern = re.compile(
            rf"{re.escape(table_var)}\.references\s+:(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
        )
        for m in ref_pattern.finditer(block):
            ref_info: Dict[str, Any] = {"reference": m.group(1)}
            options = m.group(2) or ""
            if "foreign_key: true" in options:
                ref_info["foreign_key"] = True
            if "polymorphic: true" in options:
                ref_info["polymorphic"] = True
            refs.append(ref_info)
        return refs

    # ── 4. get_template_dependencies ──────────────────────────────────

    def get_template_dependencies(self, template_path: str) -> Dict[str, Any]:
        """
        Parse ERB (render partial:, yield, content_for) and HAML (= render)
        templates. Return dependency tree.

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
            "partials": [],
            "yields": [],
            "content_for": [],
            "helpers_used": [],
            "template_engine": "unknown",
        }

        if template_path.endswith(".erb") or template_path.endswith(".html.erb"):
            dependencies["template_engine"] = "erb"

            # render partial: 'shared/header'
            partials = re.findall(
                r"render\s+(?:partial:\s*)?['\"]([^'\"]+)['\"]",
                content,
            )
            dependencies["partials"] = partials

            # yield :content_name
            yields = re.findall(r"yield\s*(?:\(\s*)?:(\w+)", content)
            dependencies["yields"] = yields
            # Plain yield (default content)
            if re.search(r"\byield\b(?!\s*:)", content):
                dependencies["yields"].insert(0, "default")

            # content_for :name
            content_fors = re.findall(r"content_for\s*(?:\(\s*)?:(\w+)", content)
            dependencies["content_for"] = content_fors

            # provide :name
            provides = re.findall(r"provide\s*(?:\(\s*)?:(\w+)", content)
            dependencies["content_for"].extend(provides)

            # Helper method calls (common patterns)
            helpers = re.findall(
                r"(?:link_to|image_tag|form_for|form_with|button_to|url_for|path_for|"
                r"stylesheet_link_tag|javascript_include_tag|turbo_frame_tag)\s*(?:\(|['\"])",
                content,
            )
            dependencies["helpers_used"] = list(set(
                re.match(r"(\w+)", h).group(1) for h in helpers if re.match(r"(\w+)", h)
            ))

        elif template_path.endswith(".haml") or template_path.endswith(".html.haml"):
            dependencies["template_engine"] = "haml"

            # = render 'partial'
            partials = re.findall(
                r"=\s*render\s+['\"]([^'\"]+)['\"]",
                content,
            )
            # = render partial: 'name'
            partials.extend(re.findall(
                r"=\s*render\s+partial:\s*['\"]([^'\"]+)['\"]",
                content,
            ))
            dependencies["partials"] = partials

            # = yield :name
            yields = re.findall(r"=\s*yield\s*(?:\(\s*)?:(\w+)", content)
            dependencies["yields"] = yields

            # = content_for :name
            content_fors = re.findall(r"=?\s*content_for\s*(?:\(\s*)?:(\w+)", content)
            dependencies["content_for"] = content_fors

        elif template_path.endswith(".slim"):
            dependencies["template_engine"] = "slim"

            partials = re.findall(
                r"==?\s*render\s+(?:partial:\s*)?['\"]([^'\"]+)['\"]",
                content,
            )
            dependencies["partials"] = partials

        # Resolve partial paths
        resolved_partials = []
        views_dir = os.path.join(base, "app", "views")
        template_dir = os.path.dirname(full_path)

        for partial in dependencies["partials"]:
            # Rails partial convention: 'shared/header' -> app/views/shared/_header.html.erb
            parts = partial.rsplit("/", 1)
            if len(parts) == 2:
                partial_dir = os.path.join(views_dir, parts[0])
                partial_name = "_" + parts[1]
            else:
                partial_dir = template_dir
                partial_name = "_" + parts[0]

            found = False
            for ext in [".html.erb", ".html.haml", ".html.slim", ".erb", ".haml", ".slim"]:
                candidate = os.path.join(partial_dir, partial_name + ext)
                if os.path.isfile(candidate):
                    resolved_partials.append({
                        "name": partial,
                        "resolved_path": os.path.relpath(candidate, base),
                        "status": "found",
                    })
                    found = True
                    break

            if not found:
                resolved_partials.append({
                    "name": partial,
                    "status": "not_found",
                })

        dependencies["resolved_partials"] = resolved_partials

        return {
            "status": "success",
            "template": dependencies,
        }

    # ── 5. get_flow_map ───────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace: Route -> before_action -> controller#action -> model -> view/JSON.

        Args:
            entry_point: Controller name or route path.
            method: Optional HTTP method or action name.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base)
        flows: List[Dict[str, Any]] = []

        # Find matching routes
        route_map = self.get_route_map()
        matching_routes = []
        if route_map.get("status") == "success":
            for route in route_map.get("routes", []):
                path_match = entry_point.lower() in route.get("path", "").lower()
                ctrl_match = entry_point.lower() in route.get("controller", "").lower()
                method_match = not method or route["method"].upper() == method.upper() or route.get("action") == method

                if (path_match or ctrl_match) and method_match:
                    matching_routes.append(route)

        if not matching_routes:
            return {"status": "error", "message": f"No routes matching '{entry_point}'"}

        for route in matching_routes:
            flow: Dict[str, Any] = {
                "route": route,
                "before_actions": [],
                "action_body": None,
                "model_calls": [],
                "render_info": None,
                "service_calls": [],
            }

            controller = route.get("controller", "")
            action = route.get("action", "")

            if not controller:
                flows.append(flow)
                continue

            # Find controller file
            ctrl_name = controller.split("/")[-1]
            ctrl_class = "".join(w.capitalize() for w in ctrl_name.split("_")) + "Controller"

            for finfo in all_files:
                if ctrl_class not in finfo["content"]:
                    continue

                content = finfo["content"]

                # Extract before_actions
                before_actions = re.findall(
                    r"before_action\s+:(\w+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
                    content,
                )
                for ba_name, ba_options in before_actions:
                    ba_info: Dict[str, Any] = {"method": ba_name}
                    if ba_options:
                        only_match = re.search(r"only:\s*\[([^\]]*)\]", ba_options)
                        except_match = re.search(r"except:\s*\[([^\]]*)\]", ba_options)
                        if only_match:
                            only_actions = re.findall(r":(\w+)", only_match.group(1))
                            ba_info["only"] = only_actions
                            if action and action not in only_actions:
                                continue
                        if except_match:
                            except_actions = re.findall(r":(\w+)", except_match.group(1))
                            ba_info["except"] = except_actions
                            if action and action in except_actions:
                                continue
                    flow["before_actions"].append(ba_info)

                # Find action method body
                if action:
                    action_pattern = re.compile(
                        rf"def\s+{re.escape(action)}\b(.*?)(?=\n\s*(?:def\s|\Z|private|protected))",
                        re.DOTALL,
                    )
                    action_match = action_pattern.search(content)
                    if action_match:
                        body = action_match.group(1)
                        flow["action_body"] = body.strip()[:500]

                        # Model calls
                        model_ops = re.findall(
                            r"(\w+)\.(find|find_by|where|create|new|update|save|destroy|"
                            r"all|first|last|count|pluck|select|order|includes|joins)\b",
                            body,
                        )
                        flow["model_calls"] = [
                            {"model": m, "operation": op} for m, op in model_ops
                            if m[0].isupper()
                        ]

                        # Service calls
                        service_calls = re.findall(
                            r"(\w+Service)(?:\.new)?\.(\w+)",
                            body,
                        )
                        flow["service_calls"] = [
                            {"service": s, "method": m} for s, m in service_calls
                        ]

                        # Render info
                        render_match = re.search(
                            r"render\s+(?:json:\s*(\S+)|(?:partial:\s*)?['\"]([^'\"]+)['\"]|"
                            r"(\w+):\s*(\S+))",
                            body,
                        )
                        if render_match:
                            flow["render_info"] = {
                                "type": "json" if render_match.group(1) else "template",
                                "target": render_match.group(1) or render_match.group(2) or render_match.group(4),
                            }

                        # Redirect
                        redirect_match = re.search(
                            r"redirect_to\s+(.+?)(?:\n|\Z)",
                            body,
                        )
                        if redirect_match:
                            flow["render_info"] = {
                                "type": "redirect",
                                "target": redirect_match.group(1).strip()[:100],
                            }

                break

            flows.append(flow)

        return {
            "status": "success",
            "entry_point": entry_point,
            "method": method,
            "flows": flows,
            "total_flows": len(flows),
        }

    # ── 6. get_cache_map ──────────────────────────────────────────────

    def get_cache_map(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Find Rails.cache.fetch/read/write, fragment caching, Russian doll caching,
        and after_commit -> cache expiry patterns.

        Args:
            model_name: Optional model name to filter.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        all_files = self._scan_all_source(base, extensions=(".rb", ".erb", ".html.erb", ".haml"))
        cache_entries: List[Dict[str, Any]] = []
        expiry_entries: List[Dict[str, Any]] = []

        for finfo in all_files:
            content = finfo["content"]
            rel_path = finfo["rel_path"]

            # Rails.cache.fetch/read/write
            cache_pattern = re.compile(
                r"Rails\.cache\.(fetch|read|write|delete)\s*\(\s*(?:['\"]([^'\"]+)['\"]|(.+?))\s*[,)]",
            )
            for m in cache_pattern.finditer(content):
                op = m.group(1)
                key = m.group(2) or m.group(3) or "dynamic_key"
                cache_entries.append({
                    "operation": op,
                    "key": key.strip()[:100],
                    "file": rel_path,
                    "line": content[:m.start()].count("\n") + 1,
                })

            # Fragment caching: cache do ... end or cache model do ... end
            fragment_pattern = re.compile(
                r"(?:<%\s*)?cache\s+(?:\(\s*)?([^)]*?)(?:\s*\))?\s*(?:do|\{|%>)",
            )
            for m in fragment_pattern.finditer(content):
                cache_entries.append({
                    "operation": "fragment_cache",
                    "key": m.group(1).strip()[:100],
                    "file": rel_path,
                    "line": content[:m.start()].count("\n") + 1,
                })

            # after_commit callbacks that expire cache
            expire_pattern = re.compile(
                r"after_commit\s+.*?(?:Rails\.cache\.delete|expire_fragment|expire_action)",
                re.DOTALL,
            )
            for m in expire_pattern.finditer(content):
                expiry_entries.append({
                    "file": rel_path,
                    "line": content[:m.start()].count("\n") + 1,
                    "text": m.group(0).strip()[:150],
                })

            # touch: true on associations (cache invalidation)
            touch_pattern = re.compile(
                r"belongs_to\s+:(\w+).*?touch:\s*true",
            )
            for m in touch_pattern.finditer(content):
                expiry_entries.append({
                    "type": "touch_invalidation",
                    "association": m.group(1),
                    "file": rel_path,
                })

        # Filter by model_name if specified
        if model_name:
            model_lower = model_name.lower()
            cache_entries = [
                c for c in cache_entries
                if model_lower in c.get("key", "").lower() or model_lower in c.get("file", "").lower()
            ]

        return {
            "status": "success",
            "cache_operations": cache_entries,
            "cache_expiry": expiry_entries,
            "total_cache_ops": len(cache_entries),
            "total_expiry_rules": len(expiry_entries),
        }

    # ── 7. get_middleware_chain ────────────────────────────────────────

    def get_middleware_chain(self, route_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Rack middleware in config/application.rb and config.middleware.use/insert.

        Args:
            route_name: Optional route name for context.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        middleware_entries: List[Dict[str, Any]] = []

        # Check config/application.rb
        config_files = [
            "config/application.rb",
            "config/environments/production.rb",
            "config/environments/development.rb",
            "config/initializers",
        ]

        order = 0
        for cf in config_files:
            full_path = os.path.join(base, cf)
            if os.path.isdir(full_path):
                for fname in os.listdir(full_path):
                    if fname.endswith(".rb"):
                        fpath = os.path.join(full_path, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                        except Exception:
                            continue
                        rel_path = os.path.relpath(fpath, base)
                        order = self._extract_middleware_from_content(content, rel_path, middleware_entries, order)
            elif os.path.isfile(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue
                rel_path = os.path.relpath(full_path, base)
                order = self._extract_middleware_from_content(content, rel_path, middleware_entries, order)

        return {
            "status": "success",
            "middleware": middleware_entries,
            "total": len(middleware_entries),
            "route_filter": route_name,
        }

    def _extract_middleware_from_content(self, content: str, rel_path: str,
                                         entries: List[Dict], order: int) -> int:
        """Extract middleware declarations from Ruby config content."""
        # config.middleware.use MiddlewareName
        use_pattern = re.compile(
            r"config\.middleware\.(use|insert_before|insert_after|insert|delete|swap)\s+(\S+)(?:\s*,\s*(.+?))?(?:\n|\Z)",
        )
        for m in use_pattern.finditer(content):
            order += 1
            entries.append({
                "order": order,
                "operation": m.group(1),
                "name": m.group(2),
                "options": m.group(3).strip() if m.group(3) else None,
                "file": rel_path,
            })

        # use MiddlewareName (in config.ru style)
        rack_use_pattern = re.compile(
            r"^\s*use\s+(\w+(?:::\w+)*)(?:\s*,\s*(.+?))?(?:\n|\Z)",
            re.MULTILINE,
        )
        for m in rack_use_pattern.finditer(content):
            order += 1
            entries.append({
                "order": order,
                "operation": "use",
                "name": m.group(1),
                "options": m.group(2).strip() if m.group(2) else None,
                "file": rel_path,
            })

        return order

    # ── 8. get_validation_chain ───────────────────────────────────────

    def get_validation_chain(self, controller: str, action: str) -> Dict[str, Any]:
        """
        Trace: strong_parameters (params.require.permit) -> model validates ->
        form_for/form_with fields.

        Args:
            controller: Controller name (e.g., "UsersController")
            action: Action name (e.g., "create")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        validation_info: Dict[str, Any] = {
            "controller": controller,
            "action": action,
            "strong_params": [],
            "model_validations": [],
            "form_fields": [],
            "mismatches": [],
        }

        all_files = self._scan_all_source(base)

        # Find controller file and extract strong params
        ctrl_class = controller if controller.endswith("Controller") else controller + "Controller"

        for finfo in all_files:
            content = finfo["content"]
            if ctrl_class not in content:
                continue

            # Find *_params method: def user_params; params.require(:user).permit(...)
            params_pattern = re.compile(
                r"def\s+(\w+_params)\b(.*?)(?=\n\s*(?:def\s|\Z|end))",
                re.DOTALL,
            )
            for m in params_pattern.finditer(content):
                method_name = m.group(1)
                body = m.group(2)

                # Extract require and permit
                require_match = re.search(r"params\.require\s*\(\s*:(\w+)\s*\)", body)
                permit_match = re.search(r"\.permit\s*\(([^)]+)\)", body)

                param_info: Dict[str, Any] = {"method": method_name}
                if require_match:
                    param_info["require"] = require_match.group(1)
                if permit_match:
                    permitted = re.findall(r":(\w+)", permit_match.group(1))
                    param_info["permitted_fields"] = permitted
                    validation_info["strong_params"].append(param_info)

            break

        # Find model validations
        permitted_model = None
        for sp in validation_info["strong_params"]:
            if "require" in sp:
                permitted_model = sp["require"]
                break

        if permitted_model:
            model_info = self.get_model_relationships(permitted_model.capitalize())
            if model_info.get("status") == "success":
                for name, data in model_info.get("models", {}).items():
                    validation_info["model_validations"] = data.get("validations", [])

        # Find form fields in views
        view_files = self._scan_files(base, self.SCAN_DIRS["views"], extensions=(".erb", ".html.erb", ".haml", ".html.haml"))
        for finfo in view_files:
            content = finfo["content"]

            # form_for/form_with field helpers
            field_pattern = re.compile(
                r"\.(\w+_field|text_area|select|check_box|radio_button|collection_select|"
                r"date_select|time_select|datetime_select|number_field|password_field|"
                r"email_field|url_field|hidden_field|file_field|color_field|range_field|"
                r"search_field|telephone_field)\s*(?:\(\s*)?:(\w+)",
            )
            for m in field_pattern.finditer(content):
                validation_info["form_fields"].append({
                    "helper": m.group(1),
                    "field": m.group(2),
                    "file": finfo["rel_path"],
                })

        # Detect mismatches
        all_permitted = set()
        for sp in validation_info["strong_params"]:
            all_permitted.update(sp.get("permitted_fields", []))

        validated_fields = {v["field"] for v in validation_info["model_validations"]}
        form_fields = {f["field"] for f in validation_info["form_fields"]}

        unvalidated = all_permitted - validated_fields
        if unvalidated:
            validation_info["mismatches"].append({
                "type": "permitted_but_unvalidated",
                "fields": sorted(unvalidated),
                "severity": "info",
                "message": "Fields permitted in strong params but no model validation",
            })

        unpermitted_form = form_fields - all_permitted
        if unpermitted_form:
            validation_info["mismatches"].append({
                "type": "form_field_not_permitted",
                "fields": sorted(unpermitted_form),
                "severity": "warning",
                "message": "Form fields not included in strong parameters",
            })

        return {
            "status": "success",
            "validation": validation_info,
        }

    # ── 9. verify_endpoint ────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify: route exists, controller#action defined, before_actions, params.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: URL path (e.g., "/users")
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
                if route["method"].upper() == method.upper() and self._rails_paths_match(route["path"], url):
                    route_found = True
                    matched_route = route
                    break

        if route_found:
            verification["checks"].append({
                "check": "route_registered",
                "status": "pass",
                "detail": f"Route found: {matched_route['controller']}#{matched_route.get('action', '')}",
            })
        else:
            verification["errors"].append({
                "check": "route_registered",
                "status": "fail",
                "detail": f"Route {method.upper()} {url} not found in config/routes.rb",
            })
            return {"status": "success", "verification": verification}

        # 2. Check controller and action exist
        controller = matched_route.get("controller", "")
        action = matched_route.get("action", "")

        if controller and action:
            ctrl_name = controller.split("/")[-1]
            ctrl_class = "".join(w.capitalize() for w in ctrl_name.split("_")) + "Controller"

            all_files = self._scan_all_source(base)
            ctrl_found = False
            action_found = False

            for finfo in all_files:
                if ctrl_class in finfo["content"]:
                    ctrl_found = True
                    verification["checks"].append({
                        "check": "controller_exists",
                        "status": "pass",
                        "detail": f"Controller {ctrl_class} found in {finfo['rel_path']}",
                    })

                    if re.search(rf"def\s+{re.escape(action)}\b", finfo["content"]):
                        action_found = True
                        verification["checks"].append({
                            "check": "action_exists",
                            "status": "pass",
                            "detail": f"Action #{action} defined",
                        })

                        # Check for before_actions
                        before_actions = re.findall(
                            r"before_action\s+:(\w+)",
                            finfo["content"],
                        )
                        if before_actions:
                            verification["checks"].append({
                                "check": "before_actions",
                                "status": "pass",
                                "detail": f"Before actions: {', '.join(before_actions[:5])}",
                            })

                        # Check for strong params
                        if re.search(r"params\.require", finfo["content"]):
                            verification["checks"].append({
                                "check": "strong_params",
                                "status": "pass",
                                "detail": "Strong parameters defined",
                            })
                        elif method.upper() in ("POST", "PUT", "PATCH"):
                            verification["warnings"].append({
                                "check": "strong_params",
                                "status": "warning",
                                "detail": "No strong parameters found for write action",
                            })
                    break

            if not ctrl_found:
                verification["errors"].append({
                    "check": "controller_exists",
                    "status": "fail",
                    "detail": f"Controller {ctrl_class} not found",
                })
            elif not action_found:
                verification["warnings"].append({
                    "check": "action_exists",
                    "status": "warning",
                    "detail": f"Action #{action} not found in {ctrl_class} (may be inherited)",
                })

        return {
            "status": "success",
            "verification": verification,
        }

    def _rails_paths_match(self, pattern: str, url: str) -> bool:
        """Check if a Rails route pattern matches a URL."""
        regex = re.sub(r":(\w+)", r"[^/]+", pattern)
        regex = "^" + regex + "$"
        try:
            return bool(re.match(regex, url))
        except re.error:
            return pattern == url

    # ── 10. get_project_conventions ───────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the Rails codebase.

        Args:
            pattern_type: One of: "naming", "testing", "concerns", "service-objects", "jobs"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_types = ["naming", "testing", "concerns", "service-objects", "jobs"]
        if pattern_type not in valid_types:
            return {"status": "error", "message": f"Invalid pattern_type. Choose from: {valid_types}"}

        conventions: Dict[str, Any] = {"pattern_type": pattern_type, "findings": []}

        if pattern_type == "naming":
            all_files = self._scan_all_source(base)
            class_names = []
            module_names = []

            for finfo in all_files:
                classes = re.findall(r"class\s+(\w+)", finfo["content"])
                modules = re.findall(r"module\s+(\w+)", finfo["content"])
                class_names.extend(classes)
                module_names.extend(modules)

            # Analyze suffixes
            suffixes: Dict[str, int] = {}
            for cls in class_names:
                for suffix in ["Controller", "Service", "Job", "Mailer", "Serializer",
                              "Decorator", "Presenter", "Policy", "Form", "Query", "Worker"]:
                    if cls.endswith(suffix):
                        suffixes[suffix] = suffixes.get(suffix, 0) + 1

            conventions["findings"].append({
                "category": "class_naming",
                "total_classes": len(class_names),
                "total_modules": len(module_names),
                "suffix_patterns": suffixes,
            })

        elif pattern_type == "testing":
            test_patterns: Dict[str, int] = {}

            # Check for RSpec
            spec_dir = os.path.join(base, "spec")
            test_dir = os.path.join(base, "test")

            if os.path.isdir(spec_dir):
                test_patterns["framework"] = "rspec"
                spec_files = self._scan_files(base, "spec")
                test_patterns["test_files"] = len(spec_files)

                for finfo in spec_files:
                    content = finfo["content"]
                    if "describe " in content:
                        test_patterns["describe_blocks"] = test_patterns.get("describe_blocks", 0) + content.count("describe ")
                    if "context " in content:
                        test_patterns["context_blocks"] = test_patterns.get("context_blocks", 0) + content.count("context ")
                    if "it " in content:
                        test_patterns["it_blocks"] = test_patterns.get("it_blocks", 0) + content.count("it ")
                    if "let(" in content or "let!(" in content:
                        test_patterns["let_blocks"] = test_patterns.get("let_blocks", 0) + 1
                    if "FactoryBot" in content or "factory" in content.lower():
                        test_patterns["factory_bot"] = test_patterns.get("factory_bot", 0) + 1
                    if "shared_examples" in content:
                        test_patterns["shared_examples"] = test_patterns.get("shared_examples", 0) + 1

            elif os.path.isdir(test_dir):
                test_patterns["framework"] = "minitest"
                test_files = self._scan_files(base, "test")
                test_patterns["test_files"] = len(test_files)

                for finfo in test_files:
                    content = finfo["content"]
                    if "def test_" in content:
                        test_patterns["test_methods"] = test_patterns.get("test_methods", 0) + content.count("def test_")
                    if "assert" in content:
                        test_patterns["assertions"] = test_patterns.get("assertions", 0) + content.count("assert")
                    if "fixtures" in content:
                        test_patterns["fixtures"] = test_patterns.get("fixtures", 0) + 1

            conventions["findings"].append({
                "category": "testing",
                "patterns": test_patterns,
            })

        elif pattern_type == "concerns":
            concern_files = self._scan_files(base, "app/models/concerns")
            concern_files.extend(self._scan_files(base, "app/controllers/concerns"))

            concerns = []
            for finfo in concern_files:
                content = finfo["content"]
                module_match = re.search(r"module\s+(\w+)", content)
                if module_match:
                    methods = re.findall(r"def\s+(\w+)", content)
                    concerns.append({
                        "name": module_match.group(1),
                        "file": finfo["rel_path"],
                        "methods": methods[:10],
                    })

            conventions["findings"].append({
                "category": "concerns",
                "total": len(concerns),
                "concerns": concerns[:20],
            })

        elif pattern_type == "service-objects":
            service_files = self._scan_files(base, "app/services")
            services = []
            for finfo in service_files:
                content = finfo["content"]
                class_match = re.search(r"class\s+(\w+)", content)
                if class_match:
                    methods = re.findall(r"def\s+(\w+)", content)
                    has_call = "def call" in content or "def self.call" in content
                    services.append({
                        "name": class_match.group(1),
                        "file": finfo["rel_path"],
                        "has_call_method": has_call,
                        "public_methods": methods[:10],
                    })

            conventions["findings"].append({
                "category": "service_objects",
                "total": len(services),
                "services": services[:20],
            })

        elif pattern_type == "jobs":
            job_files = self._scan_files(base, "app/jobs")
            jobs = []
            for finfo in job_files:
                content = finfo["content"]
                class_match = re.search(r"class\s+(\w+)", content)
                if class_match:
                    queue_match = re.search(r"queue_as\s+:(\w+)", content)
                    jobs.append({
                        "name": class_match.group(1),
                        "file": finfo["rel_path"],
                        "queue": queue_match.group(1) if queue_match else "default",
                    })

            conventions["findings"].append({
                "category": "jobs",
                "total": len(jobs),
                "jobs": jobs[:20],
            })

        return {
            "status": "success",
            "conventions": conventions,
        }

    # ── 11. get_similar_patterns ──────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns in the Rails project.

        Args:
            description: Natural language description (e.g., "service object", "mailer").
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

    # ── 12. get_project_snapshot ──────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a Rails project overview: models, controllers, routes, gems,
        migrations, jobs, mailers, channels.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "structure": {},
            "routes_summary": None,
            "models_summary": None,
            "gems": [],
            "ruby_version": None,
            "rails_version": None,
        }

        # Directory structure
        for category, directory in self.SCAN_DIRS.items():
            full_dir = os.path.join(base, directory)
            if os.path.isdir(full_dir):
                file_count = 0
                for root, dirs, files in os.walk(full_dir):
                    dirs[:] = [d for d in dirs if d not in (".git", "tmp")]
                    file_count += len([f for f in files if f.endswith(".rb")])
                snapshot["structure"][category] = {
                    "path": directory,
                    "file_count": file_count,
                }

        # Routes summary
        route_map = self.get_route_map()
        if route_map.get("status") == "success":
            routes = route_map.get("routes", [])
            method_counts: Dict[str, int] = {}
            controllers = set()
            for r in routes:
                m = r["method"]
                method_counts[m] = method_counts.get(m, 0) + 1
                if r.get("controller"):
                    controllers.add(r["controller"])
            snapshot["routes_summary"] = {
                "total": len(routes),
                "by_method": method_counts,
                "controllers": sorted(controllers)[:20],
            }

        # Models summary
        models = self.get_model_relationships()
        if models.get("status") == "success":
            snapshot["models_summary"] = {
                "total": models.get("total_models", 0),
                "models": list(models.get("models", {}).keys()),
            }

        # Gemfile
        gemfile_path = os.path.join(base, "Gemfile")
        if os.path.isfile(gemfile_path):
            try:
                with open(gemfile_path, "r", encoding="utf-8", errors="replace") as f:
                    gemfile_content = f.read()

                gems = re.findall(r"gem\s+['\"](\S+)['\"](?:\s*,\s*['\"]([^'\"]*)['\"])?", gemfile_content)
                snapshot["gems"] = [
                    {"name": g[0], "version": g[1] if g[1] else None} for g in gems
                ]

                # Rails version
                for g in snapshot["gems"]:
                    if g["name"] == "rails":
                        snapshot["rails_version"] = g.get("version")
                        break
            except Exception:
                pass

        # Ruby version
        ruby_version_file = os.path.join(base, ".ruby-version")
        if os.path.isfile(ruby_version_file):
            try:
                with open(ruby_version_file, "r") as f:
                    snapshot["ruby_version"] = f.read().strip()
            except Exception:
                pass

        return {
            "status": "success",
            "snapshot": snapshot,
        }

    # ── 13. pre_edit_check ────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware checks before editing a Rails symbol.

        Args:
            file_path: Relative file path (e.g., "app/models/user.rb")
            symbol_name: Symbol to check (e.g., "before_save", "has_many")
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

        # Context-specific impact analysis
        if "app/models" in file_path:
            checks["impact"].append({
                "type": "model_change",
                "message": f"Changing '{symbol_name}' in a model affects all dependent controllers and views",
                "severity": "high",
            })

            # Check for dependent associations
            dep_pattern = re.search(
                rf"{re.escape(symbol_name)}.*dependent:\s*:(\w+)",
                content,
            )
            if dep_pattern:
                checks["impact"].append({
                    "type": "cascade_effect",
                    "message": f"Association has dependent: :{dep_pattern.group(1)} -- may cascade deletions",
                    "severity": "high",
                })

        elif "app/controllers" in file_path:
            checks["impact"].append({
                "type": "controller_change",
                "message": f"Changing '{symbol_name}' may affect routes and views",
                "severity": "medium",
            })

        elif "app/views" in file_path:
            checks["impact"].append({
                "type": "view_change",
                "message": f"Changing '{symbol_name}' may affect rendering in controllers",
                "severity": "low",
            })

        elif "db/migrate" in file_path:
            checks["impact"].append({
                "type": "migration_change",
                "message": "Editing existing migrations is dangerous -- consider creating a new migration",
                "severity": "high",
            })

        elif "config" in file_path:
            checks["impact"].append({
                "type": "config_change",
                "message": f"Configuration changes affect the entire application",
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
        if "app/" in file_path:
            test_path = file_path.replace("app/", "test/").replace(".rb", "_test.rb")
            spec_path = file_path.replace("app/", "spec/").replace(".rb", "_spec.rb")

            has_test = os.path.isfile(os.path.join(base, test_path))
            has_spec = os.path.isfile(os.path.join(base, spec_path))

            if has_test or has_spec:
                test_file = test_path if has_test else spec_path
                checks["suggestions"].append(f"Tests exist at {test_file} -- update after changes")
            else:
                checks["suggestions"].append(f"No test file found -- consider adding tests")

        return {
            "status": "success",
            "checks": checks,
            "total_references": sum(r.get("total", 0) for r in checks["references"]),
        }
