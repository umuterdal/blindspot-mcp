"""
Laravel Intelligence Service - Laravel-specific code analysis .

Provides cross-file relationship tracking, route mapping, model relationship
extraction, and Blade dependency analysis. Designed to minimize context window
usage by returning only the data needed for accurate code generation.
"""

import logging
import os
import re
from typing import Dict, Any, List, Optional, Set

from .base_service import BaseService

logger = logging.getLogger(__name__)


class LaravelIntelligenceService(BaseService):
    """Laravel-specific code intelligence for PHP/Blade projects."""

    # Common Laravel vendor class hierarchy (stubs)
    LARAVEL_CLASS_HIERARCHY = {
        "Controller": {"extends": "BaseController", "namespace": "App\\Http\\Controllers", "vendor_file": "vendor/laravel/framework/src/Illuminate/Routing/Controller.php"},
        "Model": {"extends": None, "namespace": "Illuminate\\Database\\Eloquent", "traits": ["HasAttributes", "HasEvents", "HasGlobalScopes", "HasRelationships", "HasTimestamps", "HidesAttributes", "GuardsAttributes"]},
        "Authenticatable": {"extends": "Model", "implements": ["AuthenticatableContract", "AuthorizableContract", "CanResetPasswordContract"], "traits": ["Authenticatable", "Authorizable", "CanResetPassword", "MustVerifyEmail"]},
        "FormRequest": {"extends": "Request", "namespace": "Illuminate\\Foundation\\Http"},
        "Request": {"extends": "SymfonyRequest", "namespace": "Illuminate\\Http"},
        "Mailable": {"extends": None, "namespace": "Illuminate\\Mail", "traits": ["Queueable"]},
        "Notification": {"extends": None, "namespace": "Illuminate\\Notifications"},
        "Job": {"extends": None, "namespace": "Illuminate\\Foundation\\Bus", "traits": ["Dispatchable", "InteractsWithQueue", "Queueable", "SerializesModels"]},
        "Event": {"extends": None, "namespace": "Illuminate\\Foundation", "traits": ["Dispatchable", "InteractsWithSockets", "SerializesModels"]},
        "Middleware": {"extends": None, "namespace": "Illuminate\\Http\\Middleware"},
        "Seeder": {"extends": None, "namespace": "Illuminate\\Database\\Seeder"},
        "Migration": {"extends": None, "namespace": "Illuminate\\Database\\Migrations"},
        "Command": {"extends": "SymfonyCommand", "namespace": "Illuminate\\Console"},
        "ServiceProvider": {"extends": None, "namespace": "Illuminate\\Support"},
        "Policy": {"extends": None, "namespace": "App\\Policies"},
        "Rule": {"extends": None, "namespace": "Illuminate\\Contracts\\Validation"},
    }

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Failed to resolve project path: %s", e)
        return None

    # ── find_references ────────────────────────────────────────────────

    def find_references(self, symbol: str, scope: str = "all",
                        model_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Find all files that reference a symbol (class, method, trait, etc.).

        Unlike grep, this understands PHP imports and returns structured data
        with file paths, line numbers, and usage context.

        Args:
            symbol: Name to search for (e.g., "UserService", "processPayment", "is_active")
            scope: "all", "controllers", "models", "views", "services", "requests"
            model_context: Optional model name to filter property references.
                          When set (e.g., "Provider"), only returns references where
                          the symbol is used in the context of that model/variable.
                          Essential for common field names like "is_active", "status", "name".
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        results = {"symbol": symbol, "scope": scope, "references": [], "total": 0}
        if model_context:
            results["model_context"] = model_context

        # Build model variable patterns for context filtering
        model_patterns = None
        if model_context:
            model_patterns = self._build_model_patterns(model_context, base)

        # Determine which directories to scan
        scope_dirs = self._get_scope_dirs(scope)

        for scope_dir in scope_dirs:
            full_dir = os.path.join(base, scope_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, _, files in os.walk(full_dir):
                for fname in files:
                    if not fname.endswith(('.php', '.blade.php')):
                        continue

                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, base)

                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read %s: %s", fpath, e)
                        continue

                    if symbol not in content:
                        continue

                    lines = content.split('\n')
                    usages = []
                    for i, line in enumerate(lines, 1):
                        if symbol not in line:
                            continue

                        usage_type = self._classify_usage(line, symbol)
                        confidence = self._get_confidence(usage_type)

                        # Model context filtering
                        if model_patterns:
                            ctx_match = self._check_model_context(
                                lines, i - 1, symbol, model_context, model_patterns, content, rel_path
                            )
                            if not ctx_match["matches"]:
                                continue
                            # Upgrade confidence if context matches
                            if ctx_match.get("context_type"):
                                confidence = "high"
                                usage_type = f"{usage_type}:{ctx_match['context_type']}"

                        usages.append({
                            "line": i,
                            "text": line.strip()[:120],
                            "type": usage_type,
                            "confidence": confidence,
                        })

                    if usages:
                        results["references"].append({
                            "file": rel_path,
                            "count": len(usages),
                            "usages": usages[:10],  # Cap per file to save context
                        })

        results["total"] = sum(r["count"] for r in results["references"])
        results["files_count"] = len(results["references"])

        # Confidence summary
        high = sum(1 for r in results["references"] for u in r["usages"] if u.get("confidence") == "high")
        medium = sum(1 for r in results["references"] for u in r["usages"] if u.get("confidence") == "medium")
        low = sum(1 for r in results["references"] for u in r["usages"] if u.get("confidence") == "low")
        results["confidence_summary"] = {"high": high, "medium": medium, "low": low}

        return results

    def _build_model_patterns(self, model_name: str, base: str) -> Dict[str, Any]:
        """Build regex patterns to detect references to a specific model in code context."""
        # Variable name conventions for model
        # Provider -> $provider, $providers, $prov
        lower = model_name[0].lower() + model_name[1:]  # provider
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', model_name).lower()  # job_listing

        # Common variable patterns
        var_names = {lower, snake, f"{lower}s", f"{snake}s"}

        # Table name (pluralized snake_case)
        from ..indexing import get_index_manager
        table_name = f"{snake}s"  # Simple pluralization

        # Try to find actual table name from migration schema
        try:
            index_manager = get_index_manager()
            if index_manager.project_path:
                migrations_dir = os.path.join(base, "database", "migrations")
                if os.path.isdir(migrations_dir):
                    for mfile in sorted(os.listdir(migrations_dir)):
                        if mfile.endswith('.php'):
                            mpath = os.path.join(migrations_dir, mfile)
                            try:
                                with open(mpath, 'r', encoding='utf-8', errors='replace') as f:
                                    mcontent = f.read()
                                    m = re.search(rf"Schema::create\(['\"](\w+)['\"]", mcontent)
                                    if m and (snake in m.group(1) or lower in m.group(1)):
                                        table_name = m.group(1)
                                        break
                            except Exception as e:
                                logger.debug("Failed to read migration file: %s", e)
        except Exception as e:
            logger.debug("Failed to resolve table name from migrations: %s", e)

        # Build compiled patterns for each variable name
        var_patterns = []
        for vname in var_names:
            # $provider->is_active, $provider['is_active'], $provider->where('is_active'
            var_patterns.append(re.compile(rf'\${re.escape(vname)}\s*[->\[]+'))

        return {
            "model_name": model_name,
            "var_names": var_names,
            "var_patterns": var_patterns,
            "table_name": table_name,
            "class_pattern": re.compile(rf'\b{re.escape(model_name)}\s*::'),
            "type_hint_pattern": re.compile(rf'{re.escape(model_name)}\s+\$(\w+)'),
        }

    def _check_model_context(self, lines: List[str], line_idx: int, symbol: str,
                              model_name: str, patterns: Dict[str, Any],
                              full_content: str, file_path: str = '') -> Dict[str, Any]:
        """
        Check if a symbol usage on a specific line relates to the target model.

        Analyzes:
        1. Same-line context: $provider->is_active, Provider::where('is_active')
        2. Nearby variable context: type hints and assignments within ±10 lines
        3. Query builder context: Model::query()->where('symbol')
        4. Blade context: $provider->symbol in Blade templates
        5. File context: is this a Provider controller/service?
        """
        line = lines[line_idx]
        stripped = line.strip()

        # 1. Direct same-line patterns
        # $provider->is_active or $provider['is_active']
        for vname in patterns["var_names"]:
            if re.search(rf'\${re.escape(vname)}\s*->\s*{re.escape(symbol)}\b', stripped):
                return {"matches": True, "context_type": "property_access"}
            if re.search(rf"\${re.escape(vname)}\s*\[\s*['\"]?{re.escape(symbol)}['\"]?\s*\]", stripped):
                return {"matches": True, "context_type": "array_access"}

        # Model::where('is_active', ...) or Model::query()->where('is_active')
        if re.search(rf'\b{re.escape(model_name)}\s*::.*{re.escape(symbol)}', stripped):
            return {"matches": True, "context_type": "static_query"}

        # 2. Check for type-hinted variables in surrounding context (±15 lines)
        context_start = max(0, line_idx - 15)
        context_end = min(len(lines), line_idx + 5)
        context_block = '\n'.join(lines[context_start:context_end])

        # Find type-hinted variables: function foo(Provider $provider)
        type_hint_vars = set()
        for m in patterns["type_hint_pattern"].finditer(context_block):
            type_hint_vars.add(m.group(1))

        # Also find variables assigned from model queries
        # $provider = Provider::find(...) or $provider = Provider::where(...)
        for vname in patterns["var_names"]:
            assign_pattern = rf'\${re.escape(vname)}\s*=\s*{re.escape(model_name)}\s*::'
            if re.search(assign_pattern, context_block):
                type_hint_vars.add(vname)

        # Check if any type-hinted variable accesses the symbol on this line
        for thvar in type_hint_vars:
            if re.search(rf'\${re.escape(thvar)}\s*->\s*{re.escape(symbol)}\b', stripped):
                return {"matches": True, "context_type": "typed_property"}

        # 3. Query builder chains: ->where('is_active', true) in a Model context
        # Check if this line is part of a Model query chain
        if re.search(rf"['\"]" + re.escape(symbol) + rf"['\"]", stripped):
            # Look upward for Model::query() or Model::where() start
            for back_i in range(line_idx, max(line_idx - 10, -1), -1):
                back_line = lines[back_i].strip()
                if re.search(rf'\b{re.escape(model_name)}\s*::', back_line):
                    return {"matches": True, "context_type": "query_chain"}
                # Also check for variable that was assigned from Model
                for vname in patterns["var_names"]:
                    if re.search(rf'\${re.escape(vname)}\s*->\s*(?:where|orWhere|whereIn|firstWhere|value|pluck)', back_line):
                        return {"matches": True, "context_type": "variable_query"}
                # Stop if we hit a non-continuation line (no ->)
                if back_i < line_idx and not back_line.startswith('->') and '->' not in back_line:
                    break

        # 4. Table name in migration/schema context
        if re.search(rf"['\"]" + re.escape(patterns["table_name"]) + rf"['\"]", context_block):
            if re.search(rf"['\"]" + re.escape(symbol) + rf"['\"]", stripped):
                return {"matches": True, "context_type": "migration_column"}

        # 5. File-level context: is this file obviously about this model?
        file_rel = file_path
        # Check file path for model name
        # E.g., ProviderController, ProviderService, etc.
        containing_file = file_path

        # Check if the file imports this specific model
        model_import = rf"use\s+App\\Models\\{re.escape(model_name)}\b"
        if re.search(model_import, full_content):
            # File imports this model — check if the symbol is used with model-related vars
            for vname in patterns["var_names"]:
                if f"${vname}" in stripped:
                    return {"matches": True, "context_type": "imported_model_var"}

        # 6. Blade template context
        if "blade.php" in file_path:
            for vname in patterns["var_names"]:
                if re.search(rf'\$' + re.escape(vname) + rf'\s*->\s*' + re.escape(symbol), stripped):
                    return {"matches": True, "context_type": "blade_property"}

        return {"matches": False}

    def _classify_usage(self, line: str, symbol: str) -> str:
        """Classify how a symbol is used in a line."""
        stripped = line.strip()

        # Skip comments — low confidence
        if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('/*') or stripped.startswith('#'):
            return "comment"

        if stripped.startswith('use '):
            return "import"
        if re.search(rf'class\s+\w+.*\b{re.escape(symbol)}\b', stripped):
            return "extends_or_implements"
        if re.search(rf'{re.escape(symbol)}::', stripped):
            return "static_call"
        if re.search(rf'new\s+{re.escape(symbol)}\b', stripped):
            return "instantiation"
        if re.search(rf'->{re.escape(symbol)}\s*\(', stripped):
            return "method_call"
        if re.search(rf'function\s+{re.escape(symbol)}\s*\(', stripped):
            return "definition"
        if re.search(rf'\${re.escape(symbol)}\b', stripped):
            return "variable"

        # Check if it's inside a string literal (likely false positive)
        if re.search(rf"""['\"].*{re.escape(symbol)}.*['\"]""", stripped):
            return "string_reference"

        return "reference"

    @staticmethod
    def _get_confidence(usage_type: str) -> str:
        """Return confidence level for a usage type."""
        high_confidence = {"import", "extends_or_implements", "static_call", "instantiation", "definition"}
        medium_confidence = {"method_call", "variable", "reference"}
        low_confidence = {"comment", "string_reference"}

        if usage_type in high_confidence:
            return "high"
        elif usage_type in medium_confidence:
            return "medium"
        return "low"

    def _get_scope_dirs(self, scope: str) -> List[str]:
        """Map scope to directories."""
        scope_map = {
            "controllers": ["app/Http/Controllers"],
            "models": ["app/Models"],
            "views": ["resources/views"],
            "services": ["app/Services"],
            "requests": ["app/Http/Requests"],
            "migrations": ["database/migrations"],
        }
        if scope in scope_map:
            return scope_map[scope]
        # "all" — scan everything relevant
        return [
            "app/Http/Controllers",
            "app/Http/Middleware",
            "app/Http/Requests",
            "app/Models",
            "app/Services",
            "app/Events",
            "app/Notifications",
            "app/Providers",
            "app/Console",
            "app/Traits",
            "resources/views",
            "routes",
            "config",
            "database",
            "tests",
        ]

    # ── get_laravel_relationships ──────────────────────────────────────

    def get_laravel_relationships(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Extract Eloquent relationship map from model files.

        If model_name is provided, returns relationships for that model only.
        Otherwise returns the full relationship graph for all models.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        models_dir = os.path.join(base, "app", "Models")
        if not os.path.isdir(models_dir):
            return {"status": "error", "message": "Models directory not found"}

        relationship_types = [
            'hasOne', 'hasMany', 'belongsTo', 'belongsToMany',
            'morphTo', 'morphOne', 'morphMany', 'morphToMany',
            'morphedByMany', 'hasManyThrough', 'hasOneThrough',
        ]
        rel_pattern = re.compile(
            r'(?:public\s+)?function\s+(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{[^}]*?'
            r'\$this->(' + '|'.join(relationship_types) + r')\s*\(\s*'
            r'(?:([A-Z]\w+)::class|[\'\"]([^\'\"]+)[\'\"])',
            re.DOTALL
        )

        all_models = {}

        for fname in os.listdir(models_dir):
            if not fname.endswith('.php'):
                continue

            current_model = fname.replace('.php', '')

            if model_name and current_model != model_name:
                continue

            fpath = os.path.join(models_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read model file %s: %s", fpath, e)
                continue

            relationships = []
            for match in rel_pattern.finditer(content):
                method_name = match.group(1)
                rel_type = match.group(2)
                related_model = match.group(3) or match.group(4)

                relationships.append({
                    "method": method_name,
                    "type": rel_type,
                    "related_model": related_model,
                })

            # Also extract trait uses
            traits = re.findall(r'use\s+([\w\\]+Trait|HasFactory|Notifiable|Billable|SoftDeletes|[\w\\]*)\s*[;{]', content)
            # Filter to only actual traits (not namespace use)
            trait_uses = [t for t in traits if not t.startswith('App\\') and not t.startswith('Illuminate\\')]

            # Extract fillable/guarded
            fillable_match = re.search(r'\$fillable\s*=\s*\[([\s\S]*?)\]', content)
            fillable = []
            if fillable_match:
                fillable = re.findall(r"'(\w+)'", fillable_match.group(1))

            all_models[current_model] = {
                "file": f"app/Models/{fname}",
                "relationships": relationships,
                "traits": trait_uses,
                "fillable": fillable,
            }

        if model_name and model_name not in all_models:
            return {"status": "error", "message": f"Model '{model_name}' not found"}

        return {
            "status": "success",
            "models": all_models,
            "total_models": len(all_models),
        }

    # ── get_route_map ─────────────────────────────────────────────────

    def get_route_map(self, filter_prefix: Optional[str] = None, include_all: bool = False) -> Dict[str, Any]:
        """
        Parse route files and return route → controller → method mapping.
        Supports nested Route::prefix()->name()->middleware()->group() patterns.

        Args:
            filter_prefix: Optional route name prefix filter (e.g., "provider.career")
            include_all: If True, return full route list without context truncation
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        routes = []
        route_files = ["routes/web.php", "routes/auth.php", "routes/admin.php", "routes/api.php"]

        for route_file in route_files:
            fpath = os.path.join(base, route_file)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read route file %s: %s", fpath, e)
                continue

            self._parse_routes_recursive(content, routes, path_prefix="", name_prefix="", middleware=[])

        # Deduplicate: same method + path + name = same route
        seen = set()
        unique_routes = []
        for r in routes:
            key = (r["method"], r["path"], r["name"])
            if key not in seen:
                seen.add(key)
                unique_routes.append(r)
        routes = unique_routes

        # Apply filter
        if filter_prefix:
            routes = [r for r in routes if r.get("name", "").startswith(filter_prefix)]

        total = len(routes)

        # Context protection: if no filter and too many routes, truncate and warn
        if not include_all and not filter_prefix and total > 50:
            return {
                "status": "success",
                "routes": routes[:50],
                "total": total,
                "truncated": True,
                "warning": f"Showing 50/{total} routes. Use filter_prefix to narrow results (e.g., 'provider.', 'admin.', 'public.').",
                "available_prefixes": sorted(set(
                    r.get("name", "").split(".")[0]
                    for r in routes if r.get("name")
                ))[:20],
            }

        return {
            "status": "success",
            "routes": routes,
            "total": total,
            "filter": filter_prefix,
        }

    def _parse_routes_recursive(self, content: str, routes: List[Dict], path_prefix: str, name_prefix: str, middleware: List[str]) -> None:
        """Recursively parse route definitions including nested groups."""

        # Find group blocks: Route::prefix('x')->name('y.')->middleware([...])->group(function () { ... })
        # We need to find matching braces for the group closure
        group_pattern = re.compile(
            r"Route::"
            r"((?:(?:prefix|name|middleware)\s*\([^)]*\)\s*->\s*)*)"  # chained methods
            r"group\s*\(\s*function\s*\(\s*\)\s*\{",
            re.DOTALL
        )

        # Track positions consumed by groups so we don't double-parse routes inside them
        group_ranges = []

        # First pass: find all group ranges
        all_groups = []
        for gm in group_pattern.finditer(content):
            chain = gm.group(1)
            group_start = gm.end()
            brace_depth = 1
            pos = group_start
            while pos < len(content) and brace_depth > 0:
                if content[pos] == '{':
                    brace_depth += 1
                elif content[pos] == '}':
                    brace_depth -= 1
                pos += 1
            if brace_depth == 0:
                all_groups.append((gm.start(), pos, gm, chain, group_start, pos))

        # Only process top-level groups (not nested inside another group)
        for gm_start, gm_end, gm, chain, group_start, group_end in all_groups:
            # Skip if this group is inside another group we already found
            inside_parent = any(
                ps < gm_start < pe
                for ps, pe, *_ in all_groups
                if (ps, pe) != (gm_start, gm_end)
            )
            if inside_parent:
                continue

            # Extract prefix from chain
            prefix_match = re.search(r"prefix\s*\(\s*['\"]([^'\"]*)['\"]", chain)
            g_prefix = prefix_match.group(1) if prefix_match else ""

            # Extract name from chain
            nm = re.search(r"name\s*\(\s*['\"]([^'\"]*)['\"]", chain)
            g_name = nm.group(1) if nm else ""

            # Extract middleware from chain
            mw_match = re.search(r"middleware\s*\(\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", chain)
            g_middleware = []
            if mw_match:
                g_middleware = re.findall(r"['\"]([^'\"]+)['\"]", mw_match.group(1))

            group_body = content[group_start:group_end - 1]
            group_ranges.append((gm_start, gm_end))

            # Recurse into group body
            full_path_prefix = path_prefix.rstrip('/') + '/' + g_prefix.strip('/') if g_prefix else path_prefix
            full_name_prefix = name_prefix + g_name
            full_middleware = middleware + g_middleware

            self._parse_routes_recursive(group_body, routes, full_path_prefix, full_name_prefix, full_middleware)

        # Parse individual route definitions (not inside consumed groups)
        route_pattern = re.compile(
            r"Route::(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]*)['\"]"
            r"\s*,\s*\[\s*(?:[\w\\]*\\)?(\w+)::class\s*,\s*['\"](\w+)['\"]\s*\]\s*\)"
            r"((?:\s*->\s*\w+\s*\([^)]*\))*)",  # capture chained methods
            re.DOTALL
        )

        for rm in route_pattern.finditer(content):
            # Skip if this route is inside a group we already processed
            inside_group = any(gs <= rm.start() < ge for gs, ge in group_ranges)
            if inside_group:
                continue

            http_method = rm.group(1).upper()
            path = rm.group(2)
            controller = rm.group(3)
            action = rm.group(4)
            chain_tail = rm.group(5) or ""

            # Extract name from chain
            name_match = re.search(r"->name\s*\(\s*['\"]([^'\"]+)['\"]", chain_tail)
            route_name = name_match.group(1) if name_match else ""

            # Extract middleware from chain
            mw_match = re.search(r"->middleware\s*\(\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", chain_tail)
            route_mw = []
            if mw_match:
                route_mw = re.findall(r"['\"]([^'\"]+)['\"]", mw_match.group(1))

            # Build full path and name with prefixes
            if path_prefix:
                full_path = path_prefix.rstrip('/') + '/' + path.lstrip('/') if path != '/' else path_prefix.rstrip('/')
            else:
                full_path = path
            # Clean double slashes
            full_path = re.sub(r'/+', '/', full_path)
            if not full_path.startswith('/'):
                full_path = '/' + full_path

            full_name = name_prefix + route_name if route_name else ""
            all_mw = middleware + route_mw

            # Extract route parameters from path
            params = re.findall(r'\{(\w+)\??\}', full_path)

            route_entry = {
                "method": http_method,
                "path": full_path,
                "controller": controller,
                "action": action,
                "name": full_name,
                "middleware": all_mw if all_mw else None,
            }
            if params:
                route_entry["parameters"] = params

            routes.append(route_entry)

    # ── get_class_hierarchy ───────────────────────────────────────────

    def get_class_hierarchy(self, class_name: str) -> Dict[str, Any]:
        """
        Get the full inheritance/implementation chain for a class.

        Returns: extends, implements, use traits, and which files define them.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result = {
            "class": class_name,
            "file": None,
            "extends": None,
            "implements": [],
            "traits": [],
            "extended_by": [],
            "implemented_by": [],
        }

        # Find the class file
        for root, _, files in os.walk(os.path.join(base, "app")):
            for fname in files:
                if not fname.endswith('.php'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read %s: %s", fpath, e)
                    continue

                rel_path = os.path.relpath(fpath, base)

                # Check if this file defines the target class
                class_match = re.search(
                    rf'class\s+{re.escape(class_name)}\b'
                    r'(?:\s+extends\s+([\w\\]+))?'
                    r'(?:\s+implements\s+([\w\\,\s]+?))?'
                    r'\s*\{',
                    content
                )
                if class_match:
                    result["file"] = rel_path
                    if class_match.group(1):
                        result["extends"] = class_match.group(1)
                    if class_match.group(2):
                        result["implements"] = [
                            i.strip() for i in class_match.group(2).split(',')
                        ]

                    # Find trait uses inside the class body (not namespace imports)
                    # Class body starts at the opening brace after class declaration
                    class_body_start = content.find('{', class_match.start())
                    if class_body_start >= 0:
                        # Find trait uses only within class body scope
                        class_body = content[class_body_start:]
                        trait_pattern = re.compile(r'use\s+([\w\\]+(?:\s*,\s*[\w\\]+)*)\s*[;{]')
                        for tm in trait_pattern.finditer(class_body):
                            for trait in tm.group(1).split(','):
                                trait = trait.strip()
                                # Get just the short name
                                short_name = trait.rsplit('\\', 1)[-1] if '\\' in trait else trait
                                if short_name and short_name not in result["traits"]:
                                    result["traits"].append(short_name)

                # Check if this file extends the target class
                extends_match = re.search(
                    rf'class\s+(\w+)\s+extends\s+{re.escape(class_name)}\b',
                    content
                )
                if extends_match:
                    result["extended_by"].append({
                        "class": extends_match.group(1),
                        "file": rel_path,
                    })

                # Check if this file implements the target (if it's an interface)
                impl_match = re.search(
                    rf'class\s+(\w+)\s+.*implements\s+.*\b{re.escape(class_name)}\b',
                    content
                )
                if impl_match:
                    result["implemented_by"].append({
                        "class": impl_match.group(1),
                        "file": rel_path,
                    })

        if not result["file"]:
            return {"status": "error", "message": f"Class '{class_name}' not found in app/"}

        result["status"] = "success"

        # Resolve vendor base class hierarchy
        extends_name = result.get("extends")
        if extends_name:
            short_name = extends_name.rsplit('\\', 1)[-1] if '\\' in extends_name else extends_name
            vendor_info = self.LARAVEL_CLASS_HIERARCHY.get(short_name)
            if vendor_info:
                result["vendor_parent"] = {
                    "class": short_name,
                    "namespace": vendor_info.get("namespace"),
                    "extends": vendor_info.get("extends"),
                    "traits": vendor_info.get("traits", []),
                    "implements": vendor_info.get("implements", []),
                }

        return result

    # ── get_blade_dependencies ────────────────────────────────────────

    def get_blade_dependencies(self, view_path: str) -> Dict[str, Any]:
        """
        Get all dependencies of a Blade view file.

        Returns: parent layout, included partials, used components,
        referenced routes, Alpine.js components, and which controller
        renders this view.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, view_path)

        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"View not found: {view_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        result = {
            "view": view_path,
            "extends": None,
            "includes": [],
            "components": [],
            "component_attributes": {},
            "routes": [],
            "alpine_components": [],
            "alpine_inline_data": [],
            "alpine_global_components": [],
            "alpine_stores": [],
            "alpine_events": [],
            "alpine_dispatched_events": [],
            "alpine_refs": [],
            "alpine_models": [],
            "alpine_teleports": [],
            "alpine_store_usages": [],
            "props": [],
            "dispatched_events": [],
            "sections": [],
            "pushed_stacks": [],
            "stacks": [],
            "has_slot": False,
            "named_slots": [],
            "optional_slots": [],
            "rendered_by": [],
        }

        # Extract Blade directives
        extends_match = re.search(r"@extends\(['\"]([^'\"]+)['\"]\)", content)
        if extends_match:
            result["extends"] = extends_match.group(1)

        # Includes: @include, @includeWhen, @includeUnless, @includeFirst, @each
        for m in re.finditer(r"@(?:include(?:When|Unless|First)?|each)\(['\"]([^'\"]+)['\"]\)", content):
            if m.group(1) not in result["includes"]:
                result["includes"].append(m.group(1))

        # Extract Blade components (<x-...>) and their attributes
        comp_tag_pattern = re.compile(r'<x-([\w._-]+)([^>]*?)/?>', re.DOTALL)
        attr_pattern = re.compile(r'(:?)([\w._-]+)\s*=\s*["\']')
        for m in comp_tag_pattern.finditer(content):
            comp = m.group(1)
            attrs_str = m.group(2)
            if comp not in result["components"]:
                result["components"].append(comp)
            # Extract attributes for this component
            if attrs_str.strip():
                attrs = []
                for am in attr_pattern.finditer(attrs_str):
                    bound = am.group(1) == ':'
                    attr_name = am.group(2)
                    attrs.append({"name": attr_name, "bound": bound})
                if attrs:
                    if comp not in result["component_attributes"]:
                        result["component_attributes"][comp] = []
                    # Merge: add new attribute names not yet seen
                    existing_names = {a["name"] for a in result["component_attributes"][comp]}
                    for a in attrs:
                        if a["name"] not in existing_names:
                            result["component_attributes"][comp].append(a)
                            existing_names.add(a["name"])

        for m in re.finditer(r"route\(['\"]([^'\"]+)['\"]\)", content):
            route = m.group(1)
            if route not in result["routes"]:
                result["routes"].append(route)

        # Alpine.js named components: x-data="componentName(" or x-data='componentName('
        for m in re.finditer(r"""x-data=["'](\w+)\(""", content):
            name = m.group(1)
            if name not in result["alpine_components"]:
                result["alpine_components"].append(name)

        # Alpine.js inline data objects using brace-counting (multi-line safe)
        from blindspot.indexing.strategies.blade_strategy import BladeParsingStrategy
        for xdata in BladeParsingStrategy._extract_xdata_objects(content):
            obj_str = xdata['body']
            line = xdata['line']
            prop_names = re.findall(r'(?:^|[,\n])\s*(\w+)\s*(?::|(?:\([^)]*\)\s*\{))', obj_str)
            label = ', '.join(prop_names[:5])
            if len(prop_names) > 5:
                label += ', ...'
            result["alpine_inline_data"].append({"line": line, "properties": prop_names, "label": label})

        # Alpine.data() global component definitions
        for m in re.finditer(r"Alpine\.data\(['\"](\w+)['\"]", content):
            name = m.group(1)
            if name not in result["alpine_global_components"]:
                result["alpine_global_components"].append(name)

        # Alpine.store() definitions
        for m in re.finditer(r"Alpine\.store\(['\"](\w+)['\"]", content):
            name = m.group(1)
            if name not in result["alpine_stores"]:
                result["alpine_stores"].append(name)

        # Alpine event handlers: x-on:event and @click/@submit etc
        for m in re.finditer(r'(?:x-on:|@)([a-zA-Z][a-zA-Z0-9._-]*)(?:\.|\s*=)', content):
            event = m.group(1)
            if event not in result["alpine_events"]:
                result["alpine_events"].append(event)

        # $dispatch('event-name') → alpine_dispatched_events
        for m in re.finditer(r"\$dispatch\(['\"]([^'\"]+)['\"]", content):
            event = m.group(1)
            if event not in result["alpine_dispatched_events"]:
                result["alpine_dispatched_events"].append(event)
            # Also keep in legacy dispatched_events for backward compat
            if event not in result["dispatched_events"]:
                result["dispatched_events"].append(event)

        # x-model bindings
        for m in re.finditer(r'x-model(?:\.[\w.]+)?="([^"]+)"', content):
            model = m.group(1)
            if model not in result["alpine_models"]:
                result["alpine_models"].append(model)

        # x-ref definitions and $refs usages
        for m in re.finditer(r'x-ref="([^"]+)"', content):
            ref = m.group(1)
            if ref not in result["alpine_refs"]:
                result["alpine_refs"].append(ref)
        for m in re.finditer(r'\$refs\.(\w+)', content):
            ref = m.group(1)
            if ref not in result["alpine_refs"]:
                result["alpine_refs"].append(ref)

        # $store.storeName usages
        for m in re.finditer(r'\$store\.(\w+)', content):
            store = m.group(1)
            if store not in result["alpine_store_usages"]:
                result["alpine_store_usages"].append(store)

        # x-teleport targets
        for m in re.finditer(r'x-teleport="([^"]+)"', content):
            target = m.group(1)
            if target not in result["alpine_teleports"]:
                result["alpine_teleports"].append(target)

        # @props (component interface)
        props_match = re.search(r"@props\(\[((?:[^\[\]]*|\[[^\]]*\])*)\]\)", content, re.DOTALL)
        if props_match:
            props_str = props_match.group(1)
            # Associative: extract key before =>
            assoc_props = re.findall(r"['\"](\w+)['\"]\s*=>", props_str)
            # Remove associative parts to find indexed (standalone) props
            remaining = re.sub(r"['\"](\w+)['\"]\s*=>[^,\n]*", "", props_str)
            indexed_props = re.findall(r"['\"](\w+)['\"]", remaining)
            result["props"] = indexed_props + assoc_props

        # {{ $slot }} usage
        if re.search(r"\{\{\s*\$slot\s*\}\}", content):
            result["has_slot"] = True

        # Named slots
        named_slots = []
        for m in re.finditer(r'<x-slot\s+name=[\'"]([^\'"]+)[\'"]|<x-slot:([a-zA-Z0-9_-]+)', content):
            name = m.group(1) or m.group(2)
            if name not in named_slots:
                named_slots.append(name)
        result["named_slots"] = named_slots

        # Optional slots (detected via @isset($slotName) pattern)
        optional_slots = []
        for m in re.finditer(r'@isset\s*\(\s*\$(\w+)\s*\)', content):
            name = m.group(1)
            if name != 'slot' and name not in optional_slots:
                optional_slots.append(name)
        result["optional_slots"] = optional_slots

        for m in re.finditer(r"@section\(['\"]([^'\"]+)['\"]\)", content):
            result["sections"].append(m.group(1))

        for m in re.finditer(r"@push\(['\"]([^'\"]+)['\"]\)", content):
            result["pushed_stacks"].append(m.group(1))

        # @stack declarations (layout files)
        for m in re.finditer(r"@stack\(['\"]([^'\"]+)['\"]\)", content):
            stack = m.group(1)
            if stack not in result["stacks"]:
                result["stacks"].append(stack)

        # Find which controller renders this view
        # Convert view path to Blade dot notation
        view_name = view_path.replace('resources/views/', '').replace('.blade.php', '').replace('/', '.')

        controllers_dir = os.path.join(base, "app", "Http", "Controllers")
        if os.path.isdir(controllers_dir):
            for root, _, files in os.walk(controllers_dir):
                for fname in files:
                    if not fname.endswith('.php'):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            ctrl_content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read controller %s: %s", fpath, e)
                        continue

                    if view_name in ctrl_content:
                        rel = os.path.relpath(fpath, base)
                        # Find which method references it
                        for line_no, line in enumerate(ctrl_content.split('\n'), 1):
                            if view_name in line and 'view(' in line:
                                method_match = None
                                # Walk backwards to find the method
                                lines = ctrl_content.split('\n')
                                for j in range(line_no - 1, -1, -1):
                                    mm = re.search(r'function\s+(\w+)\s*\(', lines[j])
                                    if mm:
                                        method_match = mm.group(1)
                                        break
                                result["rendered_by"].append({
                                    "controller": rel,
                                    "method": method_match,
                                    "line": line_no,
                                })
                                break

        result["status"] = "success"
        return result

    # ── get_impact_analysis ───────────────────────────────────────────

    def get_impact_analysis(self, file_path: str) -> Dict[str, Any]:
        """
        Analyze what would be affected if a file is modified.

        Returns all files that depend on or reference the given file's
        classes, methods, and symbols. Essential for safe refactoring.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)

        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Extract all symbols defined in this file
        symbols = set()

        # Classes
        for m in re.finditer(r'class\s+(\w+)', content):
            symbols.add(m.group(1))

        # Interfaces
        for m in re.finditer(r'interface\s+(\w+)', content):
            symbols.add(m.group(1))

        # Traits
        for m in re.finditer(r'trait\s+(\w+)', content):
            symbols.add(m.group(1))

        # Enums
        for m in re.finditer(r'enum\s+(\w+)', content):
            symbols.add(m.group(1))

        if not symbols:
            return {
                "status": "success",
                "file": file_path,
                "symbols": [],
                "affected_files": [],
                "message": "No classes/interfaces/traits found in file",
            }

        # Find all files that reference these symbols
        affected: Dict[str, Dict[str, Any]] = {}
        scan_dirs = [
            "app", "routes", "resources/views", "database/migrations",
            "config", "tests",
        ]

        for scan_dir in scan_dirs:
            full_dir = os.path.join(base, scan_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, _, files in os.walk(full_dir):
                for fname in files:
                    if not fname.endswith(('.php', '.blade.php')):
                        continue

                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, base)

                    if rel == file_path:
                        continue

                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            fcontent = f.read()
                    except Exception as e:
                        logger.debug("Failed to read %s for impact analysis: %s", fpath, e)
                        continue

                    matched_symbols = [s for s in symbols if s in fcontent]
                    if matched_symbols:
                        affected[rel] = {
                            "file": rel,
                            "symbols_used": matched_symbols,
                            "is_import": any('use ' in line and any(s in line for s in matched_symbols)
                                           for line in fcontent.split('\n')),
                        }

        return {
            "status": "success",
            "file": file_path,
            "symbols": list(symbols),
            "affected_files": list(affected.values()),
            "affected_count": len(affected),
        }

    # ── get_ripple_effect ─────────────────────────────────────────────

    def get_ripple_effect(self, file_path: str, symbol: str,
                          change_type: str = "modify") -> Dict[str, Any]:
        """
        Trace the full ripple effect of changing a specific symbol in a file.

        Unlike get_impact_analysis (file-level), this is SYMBOL-level:
        "If I change Provider.is_active, what exactly breaks?"

        Returns categorized impacts:
        - direct: files that directly use this symbol on this model
        - indirect: files that use models/services that depend on this symbol
        - views: Blade templates that render this property
        - migrations: if it's a DB column, the migration that defines it
        - cache: cache keys that might go stale
        - routes: routes whose controllers use this symbol

        Args:
            file_path: File containing the symbol (e.g., "app/Models/User.php")
            symbol: Symbol name (e.g., "is_active", "scopeActive", "getFullNameAttribute")
            change_type: "modify" (signature/behavior change), "rename", "delete"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                source_content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Determine the containing class
        class_match = re.search(r'class\s+(\w+)', source_content)
        class_name = class_match.group(1) if class_match else None

        result = {
            "status": "success",
            "file": file_path,
            "symbol": symbol,
            "class": class_name,
            "change_type": change_type,
            "direct_impacts": [],
            "view_impacts": [],
            "route_impacts": [],
            "cache_impacts": [],
            "migration_impacts": [],
            "indirect_impacts": [],
            "summary": {},
        }

        # 1. Determine symbol characteristics
        symbol_info = self._analyze_symbol_type(source_content, symbol, class_name)
        result["symbol_info"] = symbol_info

        # 2. Direct impacts — find all files using this symbol with model context
        if class_name:
            direct_refs = self.find_references(symbol, scope="all", model_context=class_name)
            for ref in direct_refs.get("references", []):
                if ref["file"] != file_path:  # Exclude the defining file
                    result["direct_impacts"].append({
                        "file": ref["file"],
                        "usages": ref["usages"],
                        "count": ref["count"],
                    })

        # 3. View impacts — search Blade templates specifically
        views_dir = os.path.join(base, "resources", "views")
        if os.path.isdir(views_dir):
            blade_impacts = self._find_blade_symbol_usage(views_dir, base, symbol, class_name)
            result["view_impacts"] = blade_impacts

        # 4. Route impacts — which routes lead to affected controllers
        affected_controllers = set()
        for impact in result["direct_impacts"]:
            if "Controllers/" in impact["file"]:
                # Extract controller class name
                ctrl_name = os.path.basename(impact["file"]).replace('.php', '')
                affected_controllers.add(ctrl_name)
                # Extract methods that use the symbol
                for usage in impact["usages"]:
                    if usage.get("line"):
                        method = self._find_containing_method(
                            os.path.join(base, impact["file"]), usage["line"]
                        )
                        if method:
                            result["route_impacts"].append({
                                "controller": ctrl_name,
                                "method": method,
                                "file": impact["file"],
                                "line": usage["line"],
                            })

        # 5. Cache impacts — scan for Cache::remember keys related to this model
        if class_name:
            cache_impacts = self._find_cache_impacts(base, class_name, symbol)
            result["cache_impacts"] = cache_impacts

        # 6. Migration impacts — if symbol is a DB column, scope to model's table
        if symbol_info.get("is_property") or symbol_info.get("is_column"):
            col_name = symbol
            # Check if it's an accessor: getXxxAttribute -> xxx
            accessor_match = re.match(r'get(\w+)Attribute', symbol)
            if accessor_match:
                col_name = re.sub(r'(?<!^)(?=[A-Z])', '_', accessor_match.group(1)).lower()

            # Determine the model's table name for filtering
            table_filter = None
            if class_name:
                table_filter = self._model_to_table_name(class_name, base)

            migration_refs = self._find_column_in_migrations(base, col_name, table_filter)
            result["migration_impacts"] = migration_refs

        # 7. Indirect impacts — services/traits that depend on affected controllers
        if symbol_info.get("is_scope") or symbol_info.get("is_relationship"):
            indirect = self._find_indirect_impacts(base, class_name, symbol, symbol_info)
            result["indirect_impacts"] = indirect

        # Summary — compute totals first, then risk level
        total_affected = (
            len(result["direct_impacts"]) +
            len(result["view_impacts"]) +
            len(result["indirect_impacts"])
        )
        has_views = len(result["view_impacts"]) > 0
        has_routes = len(result["route_impacts"]) > 0
        has_cache = len(result["cache_impacts"]) > 0

        if total_affected > 20 or (has_views and has_routes and has_cache):
            risk = "critical"
        elif total_affected > 10 or (has_views and has_routes):
            risk = "high"
        elif total_affected > 5 or has_views or has_routes:
            risk = "medium"
        else:
            risk = "low"

        result["summary"] = {
            "total_files_affected": total_affected,
            "direct_files": len(result["direct_impacts"]),
            "views_affected": len(result["view_impacts"]),
            "routes_affected": len(result["route_impacts"]),
            "caches_to_clear": len(result["cache_impacts"]),
            "has_migration": len(result["migration_impacts"]) > 0,
            "risk_level": risk,
        }

        return result

    def _analyze_symbol_type(self, content: str, symbol: str, class_name: str) -> Dict[str, Any]:
        """Determine what kind of symbol this is (method, property, scope, relationship, accessor)."""
        info = {"name": symbol}

        # Check if it's a method
        method_match = re.search(rf'(?:public|protected|private)\s+function\s+{re.escape(symbol)}\s*\(', content)
        if method_match:
            info["is_method"] = True

        # Scope: scopeActive -> Eloquent scope
        if symbol.startswith('scope') and len(symbol) > 5:
            info["is_scope"] = True
            info["scope_name"] = symbol[5:6].lower() + symbol[6:]  # scopeActive -> active

        # Accessor: getXxxAttribute
        if re.match(r'get\w+Attribute', symbol):
            info["is_accessor"] = True
            raw = re.sub(r'^get|Attribute$', '', symbol)
            info["accessor_property"] = re.sub(r'(?<!^)(?=[A-Z])', '_', raw).lower()

        # Mutator: setXxxAttribute
        if re.match(r'set\w+Attribute', symbol):
            info["is_mutator"] = True

        # Relationship: returns hasOne/hasMany/belongsTo etc.
        if method_match:
            # Read method body
            rel_types = ['hasOne', 'hasMany', 'belongsTo', 'belongsToMany', 'morphTo',
                        'morphOne', 'morphMany', 'morphToMany', 'hasManyThrough', 'hasOneThrough']
            for rt in rel_types:
                if re.search(rf'\$this->{rt}\s*\(', content[method_match.start():method_match.start() + 500]):
                    info["is_relationship"] = True
                    info["relationship_type"] = rt
                    break

        # Property/column check
        if re.search(rf"['\"]" + re.escape(symbol) + rf"['\"]", content):
            # Check in $fillable, $casts, $hidden, $appends
            if re.search(rf"\$fillable\s*=\s*\[[^\]]*['\"]" + re.escape(symbol) + rf"['\"]", content, re.DOTALL):
                info["is_column"] = True
                info["in_fillable"] = True
            if re.search(rf"\$casts\s*=\s*\[[^\]]*['\"]" + re.escape(symbol) + rf"['\"]", content, re.DOTALL):
                info["is_column"] = True
                info["in_casts"] = True
            if re.search(rf"\$hidden\s*=\s*\[[^\]]*['\"]" + re.escape(symbol) + rf"['\"]", content, re.DOTALL):
                info["is_column"] = True
                info["in_hidden"] = True

        # Property (class property)
        if re.search(rf'(?:public|protected|private)\s+(?:\??\w+\s+)?\${re.escape(symbol)}\b', content):
            info["is_property"] = True

        return info

    def _find_blade_symbol_usage(self, views_dir: str, base: str,
                                  symbol: str, model_name: str) -> List[Dict[str, Any]]:
        """Find Blade template files that use a symbol in the context of a model."""
        results = []
        if not model_name:
            return results

        lower_model = model_name[0].lower() + model_name[1:]
        snake_model = re.sub(r'(?<!^)(?=[A-Z])', '_', model_name).lower()
        var_names = {lower_model, snake_model, f"{lower_model}s", f"{snake_model}s"}

        for root, _, files in os.walk(views_dir):
            for fname in files:
                if not fname.endswith('.blade.php'):
                    continue

                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read blade template %s: %s", fpath, e)
                    continue

                if symbol not in content:
                    continue

                rel = os.path.relpath(fpath, base)
                matches = []

                for i, line in enumerate(content.split('\n'), 1):
                    if symbol not in line:
                        continue
                    for vname in var_names:
                        if re.search(rf'\${re.escape(vname)}\s*->\s*{re.escape(symbol)}', line):
                            matches.append({"line": i, "text": line.strip()[:120]})
                            break

                if matches:
                    results.append({
                        "file": rel,
                        "matches": matches[:5],
                        "count": len(matches),
                    })

        return results

    def _find_containing_method(self, file_path: str, line_num: int) -> Optional[str]:
        """Find which method contains a given line number."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = list(f)
        except Exception as e:
            logger.debug("Failed to read %s for method lookup: %s", file_path, e)
            return None

        for i in range(min(line_num - 1, len(lines) - 1), -1, -1):
            m = re.search(r'function\s+(\w+)\s*\(', lines[i])
            if m:
                return m.group(1)
        return None

    def _find_cache_impacts(self, base: str, model_name: str, symbol: str) -> List[Dict[str, Any]]:
        """Find cache keys that might be affected by a model property change."""
        results = []
        lower_model = model_name.lower()

        scan_dirs = ["app"]
        for scan_dir in scan_dirs:
            full_dir = os.path.join(base, scan_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, _, files in os.walk(full_dir):
                for fname in files:
                    if not fname.endswith('.php'):
                        continue

                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read %s: %s", fpath, e)
                        continue

                    # Find Cache::remember/forget calls with keys containing the model name
                    for m in re.finditer(
                        rf"Cache::(?:remember|rememberForever|forget|put)\s*\(\s*['\"]([^'\"]*{re.escape(lower_model)}[^'\"]*)['\"]",
                        content
                    ):
                        line_no = content[:m.start()].count('\n') + 1
                        rel = os.path.relpath(fpath, base)
                        results.append({
                            "file": rel,
                            "line": line_no,
                            "cache_key": m.group(1),
                        })

        # Deduplicate by cache key
        seen = set()
        unique = []
        for r in results:
            if r["cache_key"] not in seen:
                seen.add(r["cache_key"])
                unique.append(r)
        return unique[:20]

    @staticmethod
    def _model_to_table_name(class_name: str, base: str) -> str:
        """Convert a Laravel model class name to its table name.

        Uses Laravel's convention: snake_case + pluralize.
        Also checks the model file for explicit $table property.
        """
        # First, check model file for explicit $table
        model_path = os.path.join(base, "app", "Models", f"{class_name}.php")
        if os.path.isfile(model_path):
            try:
                with open(model_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                m = re.search(r"\$table\s*=\s*['\"](\w+)['\"]", content)
                if m:
                    return m.group(1)
            except Exception as e:
                logger.debug("Failed to read model %s for table name: %s", model_path, e)

        # Laravel pluralization rules (simplified but covers common cases)
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()

        # Common English pluralization
        if snake.endswith('y') and not snake.endswith(('ay', 'ey', 'oy', 'uy')):
            return snake[:-1] + 'ies'  # category -> categories
        elif snake.endswith(('s', 'sh', 'ch', 'x', 'z')):
            return snake + 'es'
        else:
            return snake + 's'

    def _find_column_in_migrations(self, base: str, column_name: str,
                                    table_filter: str = None) -> List[Dict[str, Any]]:
        """Find migration files that define or modify a specific column.

        Args:
            base: Project base path
            column_name: Column name to search for
            table_filter: If set, only return results from migrations for this table
        """
        results = []
        migrations_dir = os.path.join(base, "database", "migrations")
        if not os.path.isdir(migrations_dir):
            return results

        for fname in sorted(os.listdir(migrations_dir)):
            if not fname.endswith('.php'):
                continue

            fpath = os.path.join(migrations_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read migration %s: %s", fpath, e)
                continue

            if column_name not in content:
                continue

            # Find the table name
            table_match = re.search(r"Schema::(?:create|table)\s*\(\s*['\"](\w+)['\"]", content)
            table = table_match.group(1) if table_match else "unknown"

            # Filter by table if specified
            if table_filter and table != table_filter:
                continue

            for m in re.finditer(rf"['\"]" + re.escape(column_name) + rf"['\"]", content):
                line_no = content[:m.start()].count('\n') + 1
                line_text = content.split('\n')[line_no - 1].strip()
                results.append({
                    "migration": fname,
                    "table": table,
                    "line": line_no,
                    "text": line_text[:120],
                })

        return results

    def _find_indirect_impacts(self, base: str, model_name: str,
                                symbol: str, symbol_info: Dict) -> List[Dict[str, Any]]:
        """Find indirect impacts through service classes and traits."""
        results = []

        # If it's a scope, find where the scope is called
        if symbol_info.get("is_scope"):
            scope_name = symbol_info.get("scope_name", "")
            if scope_name:
                # Scopes are called as ->active() not ->scopeActive()
                scope_refs = self.find_references(scope_name, scope="all")
                for ref in scope_refs.get("references", []):
                    for usage in ref.get("usages", []):
                        if usage.get("type") == "method_call":
                            results.append({
                                "file": ref["file"],
                                "line": usage["line"],
                                "text": usage["text"],
                                "impact_type": "scope_usage",
                            })

        # If it's a relationship, find where it's eager-loaded or accessed
        if symbol_info.get("is_relationship"):
            rel_refs = self.find_references(symbol, scope="all")
            for ref in rel_refs.get("references", []):
                for usage in ref.get("usages", []):
                    text = usage.get("text", "")
                    if "with(" in text or "load(" in text or f"->{symbol}" in text:
                        results.append({
                            "file": ref["file"],
                            "line": usage["line"],
                            "text": text,
                            "impact_type": "relationship_usage",
                        })

        return results[:20]

    def _assess_risk_level(self, result: Dict) -> str:
        """Assess the risk level of a change based on its ripple effect."""
        total = result["summary"]["total_files_affected"] if "summary" in result else 0
        has_views = len(result.get("view_impacts", [])) > 0
        has_routes = len(result.get("route_impacts", [])) > 0
        has_cache = len(result.get("cache_impacts", [])) > 0

        if total > 20 or (has_views and has_routes and has_cache):
            return "critical"
        elif total > 10 or (has_views and has_routes):
            return "high"
        elif total > 5 or has_views or has_routes:
            return "medium"
        return "low"

    # ── get_migration_schema ──────────────────────────────────────────

    def get_migration_schema(self, table_name: str = None) -> Dict[str, Any]:
        """
        Parse migration files and extract database schema info.

        Returns columns, indexes, foreign keys for each table.
        If table_name is provided, returns only that table's schema.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        migrations_dir = os.path.join(base, "database", "migrations")
        if not os.path.isdir(migrations_dir):
            return {"status": "error", "message": "Migrations directory not found"}

        tables: Dict[str, Dict[str, Any]] = {}

        # Sort migration files chronologically
        migration_files = sorted([
            f for f in os.listdir(migrations_dir) if f.endswith('.php')
        ])

        for fname in migration_files:
            fpath = os.path.join(migrations_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read migration %s: %s", fpath, e)
                continue

            # Find Schema::create blocks
            for create_match in re.finditer(
                r"Schema::create\(['\"](\w+)['\"]\s*,\s*function\s*\([^)]*\)\s*\{(.*?)\}\s*\)",
                content, re.DOTALL
            ):
                tbl = create_match.group(1)
                if table_name and tbl != table_name:
                    continue
                body = create_match.group(2)

                if tbl not in tables:
                    tables[tbl] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "created_in": fname,
                        "modified_in": [],
                    }

                self._parse_migration_body(body, tables[tbl])

            # Find Schema::table blocks (modifications)
            for table_match in re.finditer(
                r"Schema::table\(['\"](\w+)['\"]\s*,\s*function\s*\([^)]*\)\s*\{(.*?)\}\s*\)",
                content, re.DOTALL
            ):
                tbl = table_match.group(1)
                if table_name and tbl != table_name:
                    continue
                body = table_match.group(2)

                if tbl not in tables:
                    tables[tbl] = {
                        "columns": [],
                        "indexes": [],
                        "foreign_keys": [],
                        "created_in": "unknown",
                        "modified_in": [],
                    }

                tables[tbl]["modified_in"].append(fname)
                self._parse_migration_body(body, tables[tbl])

        if table_name:
            if table_name not in tables:
                return {"status": "error", "message": f"Table '{table_name}' not found in migrations"}
            return {
                "status": "success",
                "table": table_name,
                **tables[table_name],
            }

        return {
            "status": "success",
            "tables": {name: info for name, info in tables.items()},
            "total_tables": len(tables),
        }

    def _parse_migration_body(self, body: str, table_info: Dict[str, Any]) -> None:
        """Parse a migration function body to extract columns, indexes, and foreign keys."""

        # Column patterns: $table->type('name') or $table->type('name', length)
        column_types = [
            'id', 'bigIncrements', 'increments', 'tinyIncrements', 'smallIncrements', 'mediumIncrements',
            'string', 'text', 'mediumText', 'longText', 'tinyText',
            'integer', 'tinyInteger', 'smallInteger', 'mediumInteger', 'bigInteger',
            'unsignedInteger', 'unsignedTinyInteger', 'unsignedSmallInteger', 'unsignedMediumInteger', 'unsignedBigInteger',
            'float', 'double', 'decimal', 'unsignedDecimal',
            'boolean', 'enum', 'set',
            'date', 'dateTime', 'dateTimeTz', 'time', 'timeTz', 'timestamp', 'timestampTz',
            'year', 'binary', 'uuid', 'ulid',
            'ipAddress', 'macAddress', 'json', 'jsonb',
            'foreignId', 'foreignUuid', 'foreignUlid',
            'morphs', 'nullableMorphs', 'uuidMorphs', 'nullableUuidMorphs',
            'rememberToken', 'softDeletes', 'softDeletesTz',
            'timestamps', 'timestampsTz', 'nullableTimestamps',
        ]

        # No-arg column methods (they don't take a column name)
        no_arg_columns = {'id', 'rememberToken', 'softDeletes', 'softDeletesTz', 'timestamps', 'timestampsTz', 'nullableTimestamps'}

        existing_col_names = {c["name"] for c in table_info["columns"]}

        for col_type in column_types:
            if col_type in no_arg_columns:
                # Match $table->type() without arguments
                pattern = rf"\$table->{re.escape(col_type)}\(\)"
                for m in re.finditer(pattern, body):
                    # Determine implicit column name
                    if col_type == 'id':
                        col_name = 'id'
                    elif col_type == 'rememberToken':
                        col_name = 'remember_token'
                    elif col_type in ('softDeletes', 'softDeletesTz'):
                        col_name = 'deleted_at'
                    elif col_type in ('timestamps', 'timestampsTz', 'nullableTimestamps'):
                        # This adds created_at and updated_at
                        for ts_col in ['created_at', 'updated_at']:
                            if ts_col not in existing_col_names:
                                table_info["columns"].append({"name": ts_col, "type": "timestamp", "nullable": col_type == 'nullableTimestamps'})
                                existing_col_names.add(ts_col)
                        continue
                    else:
                        continue

                    if col_name not in existing_col_names:
                        line_text = body[m.start():].split('\n')[0].strip()
                        nullable = '->nullable()' in line_text
                        table_info["columns"].append({"name": col_name, "type": col_type, "nullable": nullable})
                        existing_col_names.add(col_name)
            else:
                # Match $table->type('column_name'...) with named column
                pattern = rf"\$table->{re.escape(col_type)}\(['\"](\w+)['\"]"
                for m in re.finditer(pattern, body):
                    col_name = m.group(1)
                    if col_name not in existing_col_names:
                        line_text = body[m.start():].split('\n')[0].strip()
                        nullable = '->nullable()' in line_text
                        unique = '->unique()' in line_text
                        default_match = re.search(r"->default\(([^)]+)\)", line_text)

                        col_info = {"name": col_name, "type": col_type, "nullable": nullable}
                        if unique:
                            col_info["unique"] = True
                        if default_match:
                            col_info["default"] = default_match.group(1).strip("'\"")

                        table_info["columns"].append(col_info)
                        existing_col_names.add(col_name)

                        # morphs adds two columns: {name}_type and {name}_id
                        if col_type in ('morphs', 'nullableMorphs', 'uuidMorphs', 'nullableUuidMorphs'):
                            for suffix in ('_type', '_id'):
                                morph_col = col_name + suffix
                                if morph_col not in existing_col_names:
                                    table_info["columns"].append({
                                        "name": morph_col,
                                        "type": "string" if suffix == '_type' else "unsignedBigInteger",
                                        "nullable": 'nullable' in col_type.lower(),
                                    })
                                    existing_col_names.add(morph_col)

        # Indexes
        for idx_match in re.finditer(r"\$table->index\(\[([^\]]+)\](?:\s*,\s*['\"](\w+)['\"])?\)", body):
            cols_str = idx_match.group(1)
            idx_name = idx_match.group(2)
            cols = re.findall(r"['\"](\w+)['\"]", cols_str)
            table_info["indexes"].append({"columns": cols, "name": idx_name, "type": "index"})

        # Single column index
        for idx_match in re.finditer(r"\$table->index\(['\"](\w+)['\"]\)", body):
            table_info["indexes"].append({"columns": [idx_match.group(1)], "type": "index"})

        # Unique indexes
        for idx_match in re.finditer(r"\$table->unique\(\[([^\]]+)\](?:\s*,\s*['\"](\w+)['\"])?\)", body):
            cols_str = idx_match.group(1)
            idx_name = idx_match.group(2)
            cols = re.findall(r"['\"](\w+)['\"]", cols_str)
            table_info["indexes"].append({"columns": cols, "name": idx_name, "type": "unique"})

        # Foreign keys
        for fk_match in re.finditer(r"\$table->foreign\(['\"](\w+)['\"]\)\s*->references\(['\"](\w+)['\"]\)\s*->on\(['\"](\w+)['\"]\)", body):
            fk_info = {
                "column": fk_match.group(1),
                "references": fk_match.group(2),
                "on": fk_match.group(3),
            }
            # Check for onDelete/onUpdate
            line_text = body[fk_match.start():].split('\n')[0]
            on_delete = re.search(r"->onDelete\(['\"](\w+)['\"]\)", line_text)
            on_update = re.search(r"->onUpdate\(['\"](\w+)['\"]\)", line_text)
            if on_delete:
                fk_info["on_delete"] = on_delete.group(1)
            if on_update:
                fk_info["on_update"] = on_update.group(1)
            table_info["foreign_keys"].append(fk_info)

        # foreignId()->constrained() shorthand
        for fk_match in re.finditer(r"\$table->foreignId\(['\"](\w+)['\"]\)[^;]*->constrained\((?:['\"](\w+)['\"])?\)", body):
            col_name = fk_match.group(1)
            ref_table = fk_match.group(2)
            if not ref_table:
                # Laravel convention: column_name -> table (remove _id suffix, pluralize)
                ref_table = col_name.replace('_id', '') + 's'
            line_text = body[fk_match.start():].split('\n')[0]
            fk_info = {
                "column": col_name,
                "references": "id",
                "on": ref_table,
            }
            on_delete = re.search(r"->(?:onDelete|cascadeOnDelete)\(['\"]?(\w*)['\"]?\)", line_text)
            if on_delete:
                fk_info["on_delete"] = on_delete.group(1) or "cascade"
            elif '->cascadeOnDelete()' in line_text:
                fk_info["on_delete"] = "cascade"
            table_info["foreign_keys"].append(fk_info)

        # Drop column tracking
        for drop_match in re.finditer(r"\$table->dropColumn\(['\"](\w+)['\"]\)", body):
            col_name = drop_match.group(1)
            # Mark as dropped but don't remove (helps understand schema evolution)
            for col in table_info["columns"]:
                if col["name"] == col_name:
                    col["dropped"] = True

    # ── get_project_snapshot ──────────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a compact snapshot of the entire project structure.
        Returns the "project brain" — all key relationships in ~5KB.

        Use as the FIRST call in every new session to understand the full codebase
        without reading any files. Covers: models, controllers, routes, views,
        services, key metrics, and cross-cutting concerns.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "status": "success",
            "metrics": {},
            "models": {},
            "controllers": {},
            "services": [],
            "routes_summary": {},
            "views_summary": {},
            "hotspots": [],
            "cross_references": {},
        }

        # 1. Project Metrics
        metrics = self._collect_metrics(base)
        snapshot["metrics"] = metrics

        # 2. Model Map — name, table, key relationships, fillable count
        models_dir = os.path.join(base, "app", "Models")
        if os.path.isdir(models_dir):
            snapshot["models"] = self._collect_model_map(models_dir)

        # 3. Controller Map — name, method count, which models they use
        controllers_dir = os.path.join(base, "app", "Http", "Controllers")
        if os.path.isdir(controllers_dir):
            snapshot["controllers"] = self._collect_controller_map(controllers_dir, base)

        # 4. Service classes
        services_dir = os.path.join(base, "app", "Services")
        if os.path.isdir(services_dir):
            snapshot["services"] = self._collect_service_list(services_dir)

        # 5. Routes summary by prefix — collect from filtered calls to avoid truncation
        prefix_filters = ["admin.", "provider.", "api."]
        all_prefixes: Dict[str, int] = {}
        seen_names: Set[str] = set()
        total_routes = 0

        for pf in prefix_filters:
            rd = self.get_route_map(pf)
            if rd.get("status") != "success":
                continue
            for r in rd.get("routes", []):
                name = r.get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    pfx = name.split(".")[0] if "." in name else "other"
                    all_prefixes[pfx] = all_prefixes.get(pfx, 0) + 1

        # Count total from route files directly (avoids truncation)
        route_files = ["routes/web.php", "routes/auth.php", "routes/admin.php", "routes/api.php"]
        for rf in route_files:
            rf_path = os.path.join(base, rf)
            if os.path.isfile(rf_path):
                try:
                    with open(rf_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    # Count Route:: definitions
                    total_routes += len(re.findall(
                        r"Route::(get|post|put|patch|delete|any|match|resource|apiResource)\s*\(",
                        content
                    ))
                except Exception as e:
                    logger.debug("Failed to read route file %s: %s", rf_path, e)

        # Remaining routes not captured by prefix filters
        counted = sum(all_prefixes.values())
        if total_routes > counted:
            all_prefixes["other"] = total_routes - counted

        snapshot["routes_summary"] = {
            "total": total_routes,
            "by_prefix": dict(sorted(all_prefixes.items(), key=lambda x: -x[1])),
        }

        # 6. Views summary by directory
        views_dir = os.path.join(base, "resources", "views")
        if os.path.isdir(views_dir):
            snapshot["views_summary"] = self._collect_views_summary(views_dir, base)

        # 7. Hotspots — largest/most complex files
        snapshot["hotspots"] = self._find_hotspots(base)

        # 8. Cross-references — model → controller → view chains
        snapshot["cross_references"] = self._build_cross_references(
            snapshot["models"], snapshot["controllers"]
        )

        return snapshot

    def _collect_metrics(self, base: str) -> Dict[str, Any]:
        """Collect high-level project metrics."""
        counts = {
            "php_files": 0, "blade_files": 0, "models": 0, "controllers": 0,
            "migrations": 0, "services": 0, "requests": 0, "middleware": 0,
        }

        dir_counts = {
            "app/Models": "models",
            "app/Http/Controllers": "controllers",
            "database/migrations": "migrations",
            "app/Services": "services",
            "app/Http/Requests": "requests",
            "app/Http/Middleware": "middleware",
        }

        for dir_rel, key in dir_counts.items():
            full = os.path.join(base, dir_rel)
            if os.path.isdir(full):
                for root, _, files in os.walk(full):
                    counts[key] += sum(1 for f in files if f.endswith('.php'))

        # Count PHP and Blade files in project (excluding vendor/storage)
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {
                'vendor', 'node_modules', 'storage', '.git', '__pycache__'
            }]
            for f in files:
                if f.endswith('.blade.php'):
                    counts["blade_files"] += 1
                elif f.endswith('.php'):
                    counts["php_files"] += 1

        return counts

    def _collect_model_map(self, models_dir: str) -> Dict[str, Any]:
        """Collect compact model information: relationships, fillable count, traits."""
        models = {}
        rel_types = [
            'hasOne', 'hasMany', 'belongsTo', 'belongsToMany',
            'morphTo', 'morphOne', 'morphMany', 'morphToMany',
            'hasManyThrough', 'hasOneThrough',
        ]
        rel_pattern = re.compile(
            r'function\s+(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{[^}]*?'
            r'\$this->(' + '|'.join(rel_types) + r')\s*\(',
            re.DOTALL
        )

        for fname in sorted(os.listdir(models_dir)):
            if not fname.endswith('.php'):
                continue
            name = fname.replace('.php', '')
            fpath = os.path.join(models_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read model %s: %s", fpath, e)
                continue

            # Relationships (compact: just name→type)
            rels = {}
            for m in rel_pattern.finditer(content):
                rels[m.group(1)] = m.group(2)

            # Fillable count
            fillable_match = re.search(r'\$fillable\s*=\s*\[([\s\S]*?)\]', content)
            fillable_count = len(re.findall(r"'(\w+)'", fillable_match.group(1))) if fillable_match else 0

            # Scopes
            scopes = re.findall(r'function\s+(scope\w+)\s*\(', content)
            scope_names = [s[5:6].lower() + s[6:] for s in scopes]  # scopeActive → active

            # Line count
            line_count = content.count('\n') + 1

            # Table name
            table = self._model_to_table_name(name, os.path.dirname(os.path.dirname(models_dir)))

            models[name] = {
                "table": table,
                "lines": line_count,
                "fillable": fillable_count,
                "relationships": rels,
                "scopes": scope_names,
            }

        return models

    def _collect_controller_map(self, controllers_dir: str, base: str) -> Dict[str, Any]:
        """Collect compact controller info: methods, which models imported."""
        controllers = {}

        for root, _, files in os.walk(controllers_dir):
            for fname in sorted(files):
                if not fname.endswith('.php'):
                    continue
                name = fname.replace('.php', '')
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, base)

                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read controller %s: %s", fpath, e)
                    continue

                # Methods
                methods = re.findall(r'public\s+function\s+(\w+)\s*\(', content)
                methods = [m for m in methods if m != '__construct']

                # Imported models
                model_imports = re.findall(r'use\s+App\\Models\\(\w+)', content)

                # Line count
                line_count = content.count('\n') + 1

                # Determine area from path
                area = "other"
                if "/Admin/" in rel:
                    area = "admin"
                elif "/Provider/" in rel:
                    area = "panel"
                elif "/Public/" in rel:
                    area = "public"
                elif "/Auth/" in rel:
                    area = "auth"
                elif "/Webhooks/" in rel:
                    area = "webhooks"
                elif "/Api/" in rel:
                    area = "api"

                controllers[name] = {
                    "file": rel,
                    "area": area,
                    "lines": line_count,
                    "methods": methods,
                    "models_used": list(set(model_imports)),
                }

        return controllers

    def _collect_service_list(self, services_dir: str) -> List[Dict[str, Any]]:
        """List service classes with their method count."""
        services = []
        for root, _, files in os.walk(services_dir):
            for fname in sorted(files):
                if not fname.endswith('.php'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read service %s: %s", fpath, e)
                    continue

                methods = re.findall(r'public\s+(?:static\s+)?function\s+(\w+)\s*\(', content)
                services.append({
                    "name": fname.replace('.php', ''),
                    "methods": len(methods),
                })
        return services

    def _collect_views_summary(self, views_dir: str, base: str) -> Dict[str, Any]:
        """Summarize views by directory with counts and large file warnings."""
        summary: Dict[str, Any] = {"total": 0, "by_directory": {}, "large_files": []}
        for root, _, files in os.walk(views_dir):
            blade_files = [f for f in files if f.endswith('.blade.php')]
            if not blade_files:
                continue
            rel_dir = os.path.relpath(root, views_dir)
            if rel_dir == ".":
                rel_dir = "root"
            summary["by_directory"][rel_dir] = len(blade_files)
            summary["total"] += len(blade_files)

            # Track large files (1000+ lines)
            for f in blade_files:
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                        lines = fh.read().count('\n') + 1
                    if lines >= 800:
                        summary["large_files"].append({
                            "file": os.path.relpath(fpath, base),
                            "lines": lines,
                        })
                except Exception as e:
                    logger.debug("Failed to count lines in %s: %s", fpath, e)

        # Sort large files descending
        summary["large_files"].sort(key=lambda x: -x["lines"])
        return summary

    def _find_hotspots(self, base: str) -> List[Dict[str, Any]]:
        """Find the most complex/largest files in the project."""
        hotspots = []
        scan_dirs = ["app/Http/Controllers", "app/Models", "app/Services"]

        for scan_dir in scan_dirs:
            full = os.path.join(base, scan_dir)
            if not os.path.isdir(full):
                continue
            for root, _, files in os.walk(full):
                for fname in files:
                    if not fname.endswith('.php'):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                        lines = content.count('\n') + 1
                        methods = len(re.findall(r'function\s+\w+\s*\(', content))
                        if lines > 200 or methods > 10:
                            hotspots.append({
                                "file": os.path.relpath(fpath, base),
                                "lines": lines,
                                "methods": methods,
                            })
                    except Exception as e:
                        logger.debug("Failed to analyze %s for hotspots: %s", fpath, e)

        hotspots.sort(key=lambda x: -x["lines"])
        return hotspots[:15]

    def _build_cross_references(self, models: Dict, controllers: Dict) -> Dict[str, Any]:
        """Build model → controller → view connection map."""
        refs: Dict[str, Dict[str, Any]] = {}

        for model_name in models:
            used_by = []
            for ctrl_name, ctrl_info in controllers.items():
                if model_name in ctrl_info.get("models_used", []):
                    used_by.append({
                        "controller": ctrl_name,
                        "area": ctrl_info.get("area", "other"),
                    })
            if used_by:
                refs[model_name] = {
                    "used_by_controllers": used_by,
                    "controller_count": len(used_by),
                }

        # Sort by most referenced
        return dict(sorted(refs.items(), key=lambda x: -x[1]["controller_count"]))

    # ── get_flow_map ──────────────────────────────────────────────────

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace a complete business flow from entry point to all effects.

        Given a controller or route name, traces the FULL chain:
        Route → Middleware → Controller method → Service calls → Model operations →
        Events dispatched → Cache impacts → View rendered → Related routes

        Args:
            entry_point: Controller name (e.g., "SubscriptionController") or
                        route name (e.g., "provider.subscription.checkout")
            method: Optional method name. If omitted and entry_point is a controller,
                   returns flow maps for all public methods.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Resolve entry point to controller file + method(s)
        controller_file, controller_name, target_methods = self._resolve_entry_point(
            base, entry_point, method
        )

        if not controller_file:
            return {"status": "error", "message": f"Could not resolve entry point: {entry_point}"}

        result = {
            "status": "success",
            "controller": controller_name,
            "file": controller_file,
            "flows": {},
        }

        # Read controller content once
        full_path = os.path.join(base, controller_file)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                controller_content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Extract imported classes
        imports = {}
        for m in re.finditer(r'use\s+([\w\\]+\\(\w+))', controller_content):
            imports[m.group(2)] = m.group(1)

        # Trace each method
        for method_name in target_methods:
            flow = self._trace_method_flow(
                base, controller_content, controller_name, controller_file,
                method_name, imports
            )
            if flow:
                result["flows"][method_name] = flow

        return result

    def _resolve_entry_point(self, base: str, entry_point: str,
                              method: Optional[str]) -> tuple:
        """Resolve entry_point to (file_path, controller_name, [methods])."""

        # Case 1: Route name (contains dots)
        if "." in entry_point:
            route_data = self.get_route_map(entry_point.rsplit(".", 1)[0] + ".")
            for r in route_data.get("routes", []):
                if r.get("name") == entry_point:
                    ctrl_name = r.get("controller", "")
                    action = r.get("action", "")
                    # Find controller file
                    ctrl_file = self._find_controller_file(base, ctrl_name)
                    if ctrl_file:
                        return (ctrl_file, ctrl_name, [action] if action else [])
            return (None, None, [])

        # Case 2: Controller name (supports namespace: "Provider/SubscriptionController")
        ctrl_name = entry_point
        if not ctrl_name.endswith("Controller"):
            ctrl_name += "Controller"

        ctrl_file = self._find_controller_file(base, ctrl_name)
        if not ctrl_file:
            return (None, None, [])

        # Extract pure class name (without namespace prefix)
        pure_name = re.split(r'[/\\]', ctrl_name)[-1]

        if method:
            return (ctrl_file, pure_name, [method])

        # Get all public methods
        full_path = os.path.join(base, ctrl_file)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            methods = re.findall(r'public\s+function\s+(\w+)\s*\(', content)
            methods = [m for m in methods if m != '__construct']
            return (ctrl_file, pure_name, methods)
        except Exception:
            return (ctrl_file, pure_name, [])

    def _find_controller_file(self, base: str, ctrl_name: str) -> Optional[str]:
        """Find a controller file by class name.

        Supports namespace prefix: 'Provider\\SubscriptionController' or
        'Provider/SubscriptionController' to disambiguate same-named controllers.
        """
        controllers_dir = os.path.join(base, "app", "Http", "Controllers")

        # Parse namespace prefix if provided
        namespace_parts = re.split(r'[/\\]', ctrl_name)
        pure_name = namespace_parts[-1]
        namespace_hint = namespace_parts[:-1] if len(namespace_parts) > 1 else []

        matches = []
        for root, _, files in os.walk(controllers_dir):
            for fname in files:
                if fname == f"{pure_name}.php":
                    rel = os.path.relpath(os.path.join(root, fname), base)
                    matches.append(rel)

        if not matches:
            return None

        if len(matches) == 1:
            return matches[0]

        # Multiple matches — use namespace hint to disambiguate
        if namespace_hint:
            hint_lower = [p.lower() for p in namespace_hint]
            for m in matches:
                path_parts = [p.lower() for p in m.replace('\\', '/').split('/')]
                if all(h in path_parts for h in hint_lower):
                    return m

        # No hint — prefer Provider > Public > Admin > other (most common usage)
        priority = ['Provider', 'Public', 'Admin', 'Auth', 'Api', 'Webhooks']
        for area in priority:
            for m in matches:
                if f"/{area}/" in m or f"\\{area}\\" in m:
                    return m

        return matches[0]

    def _find_form_request_file(self, base: str, class_name: str) -> Optional[str]:
        """Find a FormRequest file by class name in app/Http/Requests/."""
        requests_dir = os.path.join(base, "app", "Http", "Requests")
        if not os.path.isdir(requests_dir):
            return None
        for root, _, files in os.walk(requests_dir):
            for fname in files:
                if fname == f"{class_name}.php":
                    return os.path.relpath(os.path.join(root, fname), base)
        return None

    def _trace_method_flow(self, base: str, controller_content: str,
                            controller_name: str, controller_file: str,
                            method_name: str, imports: Dict[str, str]) -> Optional[Dict]:
        """Trace a single method's complete flow."""

        # Extract method body
        method_body = self._extract_method_body(controller_content, method_name)
        if not method_body:
            return None

        flow: Dict[str, Any] = {
            "route": None,
            "middleware": [],
            "request_validation": None,
            "model_operations": [],
            "service_calls": [],
            "events_dispatched": [],
            "cache_operations": [],
            "view_rendered": None,
            "redirect": None,
            "response_type": None,
        }

        # 1. Find matching route — determine area-based prefix for filtered lookup
        area_prefix_map = {
            "Admin": "admin.",
            "Provider": "provider.",
            "Auth": "",
            "Public": "",
            "Api": "api.",
            "Webhooks": "",
        }
        route_prefix = ""
        for area_key, prefix in area_prefix_map.items():
            if f"/{area_key}/" in controller_file or f"\\{area_key}\\" in controller_file:
                route_prefix = prefix
                break

        route_data = self.get_route_map(route_prefix if route_prefix else None)
        for r in route_data.get("routes", []):
            if (r.get("controller") == controller_name and
                r.get("action") == method_name):
                flow["route"] = {
                    "method": r.get("method"),
                    "path": r.get("path"),
                    "name": r.get("name"),
                    "middleware": r.get("middleware", []),
                }
                flow["middleware"] = r.get("middleware", [])
                break

        # 2. Request validation (Form Request or $request->validate)
        form_request_match = re.search(r'(\w+Request)\s+\$request', method_body)
        if form_request_match:
            fr_class = form_request_match.group(1)
            fr_info: Dict[str, Any] = {"class": fr_class}
            # Try to find and read the FormRequest file to extract rules()
            fr_file = self._find_form_request_file(base, fr_class)
            if fr_file:
                fr_info["file"] = fr_file
                try:
                    fr_full_path = os.path.join(base, fr_file)
                    with open(fr_full_path, 'r', encoding='utf-8', errors='replace') as f:
                        fr_content = f.read()
                    rules_body = self._extract_method_body(fr_content, 'rules')
                    if rules_body:
                        fr_info["rules"] = rules_body
                except Exception as e:
                    logger.debug("Failed to read form request for flow map: %s", e)
            flow["request_validation"] = fr_info
        elif 'validate(' in method_body:
            flow["request_validation"] = "inline_validation"

        # 3. Model operations
        model_ops = []

        # Direct model calls: Model::method()
        for m in re.finditer(r'(\w+)::(find|findOrFail|where|create|query|with|all|first|firstOrFail|count|paginate|get)\b', method_body):
            model_name = m.group(1)
            if model_name in imports or (model_name[0].isupper() and model_name not in {'Cache', 'Log', 'DB', 'Auth', 'Session', 'Setting', 'Str', 'Carbon', 'Validator', 'Hash', 'Mail', 'Notification'}):
                model_ops.append({
                    "model": model_name,
                    "operation": m.group(2),
                })

        # Facade calls: Auth::user(), Setting::get(), DB::transaction()
        facade_ops = []
        for m in re.finditer(r'(Auth|DB|Setting)::(user|guard|id|check|transaction|table|select|get)\s*\(', method_body):
            facade_ops.append({"facade": m.group(1), "operation": m.group(2)})
        if facade_ops:
            model_ops.extend(facade_ops[:5])

        # Variable method calls: $model->save(), ->update(), ->delete()
        for m in re.finditer(r'\$\w+->(save|update|delete|create|forceDelete|restore|increment|decrement|lockForUpdate|first|firstOrFail)\s*\(', method_body):
            model_ops.append({"operation": m.group(1)})

        # Relationship eager loading: ->with([...]) and ->with('...')
        # Exclude session flash: ->with('error', ...), ->with('success', ...), withErrors(...)
        flash_keys = {'error', 'errors', 'success', 'warning', 'info', 'message', 'status'}
        for m in re.finditer(r'->with\(\[([^\]]+)\]', method_body):
            relations = re.findall(r"'(\w+)'", m.group(1))
            for rel in relations:
                if rel not in flash_keys:
                    model_ops.append({"operation": "eager_load", "relationship": rel})
        for m in re.finditer(r"->with\(\s*'(\w+)'(?:\s*\)|\s*,\s*\[)", method_body):
            rel = m.group(1)
            if rel not in flash_keys:
                model_ops.append({"operation": "eager_load", "relationship": rel})

        # Collection operations on cached/queried data
        for m in re.finditer(r'\$\w+->(firstWhere|where|pluck|filter|map|each|contains|count|isEmpty|isNotEmpty)\s*\(', method_body):
            op = m.group(1)
            if op not in {'count', 'isEmpty', 'isNotEmpty'}:  # skip trivial checks
                model_ops.append({"operation": f"collection_{op}"})

        flow["model_operations"] = model_ops[:20]

        # 4. Service calls — direct and via app() helper
        seen_services: Set[str] = set()
        for m in re.finditer(r'(\w+(?:Service|Gateway))(?:::\w+|\s*->\s*\w+)\s*\(', method_body):
            svc_name = m.group(1)
            svc_method_match = re.search(rf'{re.escape(svc_name)}(?:::|->\s*)(\w+)', method_body[m.start():])
            svc_method = svc_method_match.group(1) if svc_method_match else "unknown"
            key = f"{svc_name}::{svc_method}"
            if key not in seen_services:
                seen_services.add(key)
                flow["service_calls"].append({"service": svc_name, "method": svc_method})
        # app(Service::class)->method() pattern
        for m in re.finditer(r'app\([^)]*\\(\w+(?:Service|Gateway))::class\)\s*->\s*(\w+)', method_body):
            key = f"{m.group(1)}::{m.group(2)}"
            if key not in seen_services:
                seen_services.add(key)
                flow["service_calls"].append({"service": m.group(1), "method": m.group(2)})
        # $this->property (injected service) pattern
        for m in re.finditer(r'\$this->(\w+)->\s*(\w+)\s*\(', method_body):
            prop = m.group(1)
            # Check if property name suggests a service (payment, seo, notification, etc.)
            if prop in {'payment', 'seo', 'notification', 'statistics', 'sidebar', 'sitemap', 'deletion', 'twoFactor', 'push'}:
                key = f"${prop}::{m.group(2)}"
                if key not in seen_services:
                    seen_services.add(key)
                    flow["service_calls"].append({"service": f"${prop}", "method": m.group(2)})

        # 5. Events dispatched
        for m in re.finditer(r'(?:event|broadcast|dispatch)\s*\(\s*new\s+(\w+)', method_body):
            flow["events_dispatched"].append(m.group(1))
        # Static dispatch
        for m in re.finditer(r'(\w+)::(?:dispatch|broadcast|safe)\s*\(', method_body):
            event_name = m.group(1)
            if event_name not in {'Cache', 'Log', 'DB', 'Auth', 'Session'}:
                flow["events_dispatched"].append(event_name)

        # 6. Cache operations
        for m in re.finditer(r"Cache::(remember|forget|put|flush|rememberForever)\s*\(\s*['\"]([^'\"]*)['\"]", method_body):
            flow["cache_operations"].append({
                "action": m.group(1),
                "key": m.group(2),
            })

        # 7. View rendered
        view_match = re.search(r"view\s*\(\s*['\"]([^'\"]+)['\"]", method_body)
        if view_match:
            flow["view_rendered"] = view_match.group(1)
            flow["response_type"] = "view"
        elif 'redirect(' in method_body or 'redirect()->' in method_body:
            redirect_match = re.search(r"redirect\(\)->route\(['\"]([^'\"]+)['\"]", method_body)
            if redirect_match:
                flow["redirect"] = redirect_match.group(1)
            flow["response_type"] = "redirect"
        elif 'response()->json' in method_body or 'JsonResponse' in method_body:
            flow["response_type"] = "json"
        elif 'return back()' in method_body:
            flow["response_type"] = "back"
        else:
            flow["response_type"] = "other"

        # Clean up empty fields
        return {k: v for k, v in flow.items() if v}

    def _extract_method_body(self, content: str, method_name: str) -> Optional[str]:
        """Extract a method's body from file content."""
        pattern = re.compile(
            rf'(?:public|protected|private)\s+function\s+{re.escape(method_name)}\s*\([^)]*\)[^{{]*\{{',
            re.DOTALL
        )
        match = pattern.search(content)
        if not match:
            return None

        start = match.end()
        brace_depth = 1
        pos = start
        while pos < len(content) and brace_depth > 0:
            if content[pos] == '{':
                brace_depth += 1
            elif content[pos] == '}':
                brace_depth -= 1
            pos += 1

        if brace_depth == 0:
            return content[match.start():pos]
        return None

    # ------------------------------------------------------------------ #
    #  verify_schema / detect_transaction_risks / get_domain_rules /      #
    #  generate_test_skeleton / match_view_guards                         #
    # ------------------------------------------------------------------ #

    def verify_schema(self, table_or_model: str, columns: list) -> Dict[str, Any]:
        """
        Verify that columns exist in migration-defined schema for a table or model.

        Returns missing columns so the caller knows what is absent before editing.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        migrations_dir = os.path.join(base, "database", "migrations")
        if not os.path.isdir(migrations_dir):
            return {"status": "ok", "table": table_or_model, "found": [], "missing": list(columns),
                    "message": "Migrations directory not found – cannot verify"}

        # Normalise: if a Model name is given, snake-case + pluralise naively
        table_name = table_or_model
        if table_name[0:1].isupper():
            # CamelCase → snake_case rough conversion
            snake = re.sub(r'(?<!^)(?=[A-Z])', '_', table_name).lower()
            table_name = snake + "s" if not snake.endswith("s") else snake

        found_columns: Set[str] = set()
        migration_files = sorted(
            [f for f in os.listdir(migrations_dir) if f.endswith(".php")]
        )

        col_pattern = re.compile(
            r"\$\w+->(string|integer|bigInteger|text|boolean|date|dateTime|timestamp|"
            r"float|decimal|json|uuid|char|binary|enum|unsignedBigInteger|unsignedInteger|"
            r"increments|bigIncrements|id|timestamps|softDeletes|morphs|nullableMorphs|"
            r"foreignId|foreignUuid|tinyInteger|smallInteger|mediumInteger|mediumText|"
            r"longText|double)\s*\(\s*['\"](\w+)['\"]",
            re.IGNORECASE,
        )
        shorthand_pattern = re.compile(
            r"\$\w+->(timestamps|softDeletes|rememberToken|id)\s*\(",
        )

        for fname in migration_files:
            fpath = os.path.join(migrations_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Failed to read migration %s: %s", fpath, e)
                continue

            # Only look at blocks that reference our table
            if table_name not in content:
                continue

            for m in col_pattern.finditer(content):
                found_columns.add(m.group(2))

            for m in shorthand_pattern.finditer(content):
                method = m.group(1)
                if method == "timestamps":
                    found_columns.update(["created_at", "updated_at"])
                elif method == "softDeletes":
                    found_columns.add("deleted_at")
                elif method == "rememberToken":
                    found_columns.add("remember_token")
                elif method == "id":
                    found_columns.add("id")

        found = [c for c in columns if c in found_columns]
        missing = [c for c in columns if c not in found_columns]

        return {
            "status": "ok",
            "table": table_name,
            "found": found,
            "missing": missing,
            "all_schema_columns": sorted(found_columns),
        }

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """
        Detect transaction / atomicity risks in a Laravel PHP file.

        Looks for multiple DB writes without DB::transaction(), and cache
        operations inside transactions.
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
        lines = content.split("\n")

        # Detect DB write patterns
        write_pattern = re.compile(
            r"(->save\s*\(|->create\s*\(|->update\s*\(|->delete\s*\(|->insert\s*\(|"
            r"DB::insert|DB::update|DB::delete|->forceDelete\s*\()"
        )
        transaction_pattern = re.compile(r"DB::transaction\s*\(|DB::beginTransaction\s*\(")
        cache_pattern = re.compile(r"Cache::(put|forget|flush|remember|rememberForever|set|add)\s*\(")

        # Find methods and check for multiple writes without transaction
        method_pattern = re.compile(
            r"(?:public|protected|private)\s+function\s+(\w+)\s*\([^)]*\)\s*\{",
        )
        for m_match in method_pattern.finditer(content):
            method_name = m_match.group(1)
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
                    "message": f"Method '{method_name}' has {len(write_calls)} DB writes without DB::transaction()",
                    "severity": "high",
                })

            # Cache inside transaction
            if has_transaction:
                cache_calls = cache_pattern.findall(method_body)
                if cache_calls:
                    line_num = content[:m_match.start()].count("\n") + 1
                    risks.append({
                        "type": "cache_in_transaction",
                        "method": method_name,
                        "line": line_num,
                        "message": f"Cache operations inside DB::transaction() in '{method_name}' – "
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
        Return domain-aware anti-pattern rules based on the file location / type.
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
            except Exception as e:
                logger.debug("Failed to read %s for conventions: %s", full_path, e)

        # Controllers handling public pages → SEO
        if "controllers" in norm:
            rules.append({
                "rule": "require_seo_meta",
                "message": "Public-facing controller actions should set SEO meta (title, description)",
                "severity": "info",
            })
            if not re.search(r"abort_unless|abort_if|authorize|middleware|->can\(", content):
                rules.append({
                    "rule": "require_authorization",
                    "message": "Controller has no authorization checks (abort_unless, authorize, middleware)",
                    "severity": "warning",
                })

        # Webhook controllers → require transaction
        if "webhook" in norm:
            rules.append({
                "rule": "require_transaction",
                "message": "Webhook handlers should wrap DB writes in DB::transaction()",
                "severity": "high",
            })
            rules.append({
                "rule": "require_idempotency",
                "message": "Webhook handlers should be idempotent – check for duplicate event processing",
                "severity": "high",
            })

        # Models → require $fillable
        if "models" in norm and norm.endswith(".php"):
            if content and not re.search(r"\$fillable\s*=", content):
                rules.append({
                    "rule": "require_fillable",
                    "message": "Eloquent model should declare $fillable to prevent mass-assignment vulnerabilities",
                    "severity": "high",
                })
            if content and not re.search(r"\$casts\s*=", content):
                rules.append({
                    "rule": "recommend_casts",
                    "message": "Consider declaring $casts for type-safe attribute access",
                    "severity": "info",
                })

        # Services → stateless
        if "services" in norm:
            if content and re.search(r"\$this->\w+\s*=\s*", content):
                rules.append({
                    "rule": "stateless_service",
                    "message": "Service classes should be stateless – avoid storing mutable state in properties",
                    "severity": "warning",
                })

        # Middleware
        if "middleware" in norm:
            rules.append({
                "rule": "middleware_performance",
                "message": "Middleware runs on every matching request – avoid heavy DB/IO operations",
                "severity": "info",
            })

        return {
            "status": "ok",
            "file": file_path,
            "rules": rules,
            "rule_count": len(rules),
        }

    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Generate a PHPUnit test skeleton for a given function/method in a Laravel project.
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

        # Extract class name
        class_match = re.search(r"class\s+(\w+)", content)
        class_name = class_match.group(1) if class_match else "Unknown"

        # Extract method signature
        method_match = re.search(
            rf"(?:public|protected|private)\s+function\s+{re.escape(symbol)}\s*\(([^)]*)\)",
            content,
        )
        params = method_match.group(1).strip() if method_match else ""

        # Determine test type
        if "controllers" in norm:
            test_type = "feature"
            test_class = f"{class_name}Test"
            test_body = self._generate_controller_test(class_name, symbol, content)
        elif "models" in norm:
            test_type = "unit"
            test_class = f"{class_name}Test"
            test_body = self._generate_model_test(class_name, symbol)
        elif "services" in norm or "actions" in norm:
            test_type = "unit"
            test_class = f"{class_name}Test"
            test_body = self._generate_service_test(class_name, symbol, params)
        else:
            test_type = "unit"
            test_class = f"{class_name}Test"
            test_body = self._generate_generic_test(class_name, symbol, params)

        skeleton = (
            f"<?php\n\nnamespace Tests\\{test_type.capitalize()};\n\n"
            f"use Tests\\TestCase;\n"
            f"use Illuminate\\Foundation\\Testing\\RefreshDatabase;\n\n"
            f"class {test_class} extends TestCase\n{{\n"
            f"    use RefreshDatabase;\n\n"
            f"{test_body}"
            f"}}\n"
        )

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "test_type": test_type,
            "test_class": test_class,
            "skeleton": skeleton,
        }

    def _generate_controller_test(self, class_name: str, method: str, content: str) -> str:
        """Generate test body for a controller method."""
        # Try to find the route
        route_match = re.search(
            rf"Route::(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"].*{re.escape(class_name)}",
            content,
        )
        http_method = route_match.group(1) if route_match else "get"
        route_path = route_match.group(2) if route_match else f"/{method}"

        return (
            f"    public function test_{method}_returns_success(): void\n"
            f"    {{\n"
            f"        $response = $this->{http_method}('{route_path}');\n\n"
            f"        $response->assertStatus(200);\n"
            f"    }}\n\n"
            f"    public function test_{method}_requires_authentication(): void\n"
            f"    {{\n"
            f"        $response = $this->{http_method}('{route_path}');\n\n"
            f"        $response->assertRedirect('/login');\n"
            f"    }}\n"
        )

    def _generate_model_test(self, class_name: str, method: str) -> str:
        """Generate test body for a model method."""
        return (
            f"    public function test_{method}(): void\n"
            f"    {{\n"
            f"        $model = {class_name}::factory()->create();\n\n"
            f"        $result = $model->{method}();\n\n"
            f"        $this->assertNotNull($result);\n"
            f"    }}\n"
        )

    def _generate_service_test(self, class_name: str, method: str, params: str) -> str:
        """Generate test body for a service method."""
        return (
            f"    private ${class_name[0].lower() + class_name[1:]};\n\n"
            f"    protected function setUp(): void\n"
            f"    {{\n"
            f"        parent::setUp();\n"
            f"        $this->{class_name[0].lower() + class_name[1:]} = app({class_name}::class);\n"
            f"    }}\n\n"
            f"    public function test_{method}(): void\n"
            f"    {{\n"
            f"        // Arrange\n"
            f"        // TODO: set up test data\n\n"
            f"        // Act\n"
            f"        $result = $this->{class_name[0].lower() + class_name[1:]}->{method}();\n\n"
            f"        // Assert\n"
            f"        $this->assertNotNull($result);\n"
            f"    }}\n"
        )

    def _generate_generic_test(self, class_name: str, method: str, params: str) -> str:
        """Generate test body for a generic method."""
        return (
            f"    public function test_{method}(): void\n"
            f"    {{\n"
            f"        $instance = new {class_name}();\n\n"
            f"        $result = $instance->{method}();\n\n"
            f"        $this->assertNotNull($result);\n"
            f"    }}\n"
        )

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Cross-reference controller abort_unless/if conditions with matching
        @if/@unless conditions in Blade templates.
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

        # Extract guard conditions from the controller/method
        guards: List[Dict[str, Any]] = []
        guard_patterns = [
            (r"abort_unless\s*\(\s*(.+?)\s*,", "abort_unless"),
            (r"abort_if\s*\(\s*(.+?)\s*,", "abort_if"),
            (r"\$this->authorize\s*\(\s*['\"](\w+)['\"]", "authorize"),
            (r"->can\s*\(\s*['\"](\w+)['\"]", "can"),
            (r"Gate::allows\s*\(\s*['\"](\w+)['\"]", "gate_allows"),
            (r"Gate::denies\s*\(\s*['\"](\w+)['\"]", "gate_denies"),
        ]

        for pattern, guard_type in guard_patterns:
            for m in re.finditer(pattern, content):
                line_num = content[:m.start()].count("\n") + 1
                guards.append({
                    "type": guard_type,
                    "condition": m.group(1),
                    "line": line_num,
                })

        # Search Blade templates for matching conditions
        views_dir = os.path.join(base, "resources", "views")
        blade_matches: List[Dict[str, Any]] = []

        if os.path.isdir(views_dir):
            blade_patterns = [
                (r"@if\s*\(\s*(.+?)\s*\)", "blade_if"),
                (r"@unless\s*\(\s*(.+?)\s*\)", "blade_unless"),
                (r"@can\s*\(\s*['\"](\w+)['\"]", "blade_can"),
                (r"@cannot\s*\(\s*['\"](\w+)['\"]", "blade_cannot"),
            ]

            for root, _dirs, files in os.walk(views_dir):
                for fname in files:
                    if not fname.endswith(".blade.php"):
                        continue
                    blade_path = os.path.join(root, fname)
                    try:
                        with open(blade_path, "r", encoding="utf-8", errors="replace") as f:
                            blade_content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read blade template %s: %s", blade_path, e)
                        continue

                    rel_blade = os.path.relpath(blade_path, base)
                    for bp, btype in blade_patterns:
                        for bm in re.finditer(bp, blade_content):
                            blade_cond = bm.group(1)
                            blade_line = blade_content[:bm.start()].count("\n") + 1
                            # Check if any guard condition is referenced in the Blade condition
                            for guard in guards:
                                cond_text = guard["condition"]
                                if (cond_text in blade_cond or
                                        blade_cond in cond_text or
                                        (guard["type"] in ("authorize", "can", "gate_allows") and
                                         btype in ("blade_can",) and
                                         guard["condition"] == blade_cond)):
                                    blade_matches.append({
                                        "blade_file": rel_blade,
                                        "blade_line": blade_line,
                                        "blade_type": btype,
                                        "blade_condition": blade_cond,
                                        "backend_guard": guard,
                                    })

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "backend_guards": guards,
            "blade_matches": blade_matches,
            "matched_count": len(blade_matches),
            "unmatched_guards": len(guards) - len({m["backend_guard"]["line"] for m in blade_matches}),
        }
