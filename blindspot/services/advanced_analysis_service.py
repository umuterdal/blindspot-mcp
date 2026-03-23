"""
Advanced Analysis Service - Higher-level code analysis tools.

Provides advanced analysis capabilities that go beyond basic code intelligence:
- Query performance analysis (N+1 detection, missing indexes, smart table resolution)
- Semantic symbol renaming across files (word-boundary safe)
- Eager loading audit (controller + Blade, severity-aware by relationship type)
- Automatic anti-pattern enforcement on apply_edit
- Cache key conflict detection (independent reader scanning)
- Multi-file diff preview (dry-run)
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService
from .generic_intelligence_service import GenericIntelligenceService
from .laravel_intelligence_service import LaravelIntelligenceService
from .laravel_validation_service import LaravelValidationService

logger = logging.getLogger(__name__)

# Laravel irregular pluralization — covers common model names
_IRREGULAR_PLURALS = {
    "Category": "categories",
    "City": "cities",
    "Country": "countries",
    "Company": "companies",
    "Activity": "activities",
    "Reply": "replies",
    "Entry": "entries",
    "Family": "families",
    "Proxy": "proxies",
    "Query": "queries",
    "Index": "indices",
    "Status": "statuses",
    "Address": "addresses",
    "Process": "processes",
    "Tax": "taxes",
    "Fax": "faxes",
    "Quiz": "quizzes",
    "Bus": "buses",
    "Dish": "dishes",
    "Batch": "batches",
    "Match": "matches",
    "Patch": "patches",
    "Switch": "switches",
    "Person": "people",
    "Child": "children",
    "Man": "men",
    "Woman": "women",
    "Medium": "media",
}

# Relationships where N+1 is harmless — belongsTo is a single query, not N
_SAFE_RELATIONSHIP_TYPES = {"belongsTo", "morphTo"}


class AdvancedAnalysisService(BaseService):
    """Advanced analysis tools combining multiple intelligence methods."""

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
        return None

    def _get_intel(self) -> LaravelIntelligenceService:
        """Create a LaravelIntelligenceService instance (for Laravel-specific analysis)."""
        return LaravelIntelligenceService(self.ctx)

    def _get_generic_intel(self) -> GenericIntelligenceService:
        """Create a GenericIntelligenceService instance (language-agnostic)."""
        return GenericIntelligenceService(self.ctx)

    def _get_validator(self) -> LaravelValidationService:
        """Create a LaravelValidationService instance sharing the same context."""
        return LaravelValidationService(self.ctx)

    def _read_file(self, full_path: str) -> Optional[str]:
        """Read file content safely."""
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    # ── analyze_queries ──────────────────────────────────────────────

    def analyze_queries(self, controller: str, method: str = None) -> Dict[str, Any]:
        """
        Analyze Eloquent queries in a controller method for performance issues.

        Detects:
        - N+1 query risks (relationship access without eager loading)
        - Missing database indexes on filtered/sorted columns
        - ->get() without pagination on list endpoints
        - Queries inside loops
        - Missing ->select() (fetching all columns)

        Uses model $table property and migration schema for accurate table resolution.

        Args:
            controller: Controller name (e.g., "UserController" or "Admin/OrderController")
            method: Optional method name. If omitted, analyzes ALL public methods.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        controller_file = self._find_controller_file(base, controller)
        if not controller_file:
            return {"status": "error", "message": f"Controller not found: {controller}"}

        content = self._read_file(controller_file)
        if not content:
            return {"status": "error", "message": f"Could not read: {controller_file}"}

        rel_path = os.path.relpath(controller_file, base)
        issues: List[Dict[str, Any]] = []

        methods_to_check = self._extract_methods(content, method)
        if not methods_to_check:
            msg = f"Method '{method}' not found" if method else "No public methods found"
            return {"status": "error", "message": msg}

        intel = self._get_intel()

        # Build model -> table map (reads $table property from each model file)
        imported_models = self._find_imported_models(content)
        model_table_map = self._build_model_table_map(base, imported_models)

        # Get migration schema for index checking
        schema_data = intel.get_migration_schema()
        all_indexes = self._extract_all_indexes(schema_data)

        # Get model relationship info with types
        model_relationships: Dict[str, Dict[str, str]] = {}  # model -> {rel_name: rel_type}
        for model_name in imported_models:
            try:
                rel_data = intel.get_laravel_relationships(model_name)
                if rel_data.get("status") == "success":
                    models = rel_data.get("models", {})
                    if model_name in models:
                        rels = models[model_name].get("relationships", [])
                        model_relationships[model_name] = {
                            r.get("name", ""): r.get("type", "")
                            for r in rels
                        }
            except Exception:
                pass

        for method_name, method_body, start_line in methods_to_check:
            method_lines = method_body.split("\n")

            # Find with() eager loads for this method
            with_calls = re.findall(r'->with\(\s*\[?\s*([\'"][^)]+)\s*\]?\s*\)', method_body)
            eager_loaded: Set[str] = set()
            for wc in with_calls:
                for rel in re.findall(r"['\"](\w+)['\"]", wc):
                    eager_loaded.add(rel)
            # withCount also counts
            for wc in re.findall(r"->withCount\(\s*\[?\s*([^)]+)\s*\]?\s*\)", method_body):
                for rel in re.findall(r"['\"](\w+)['\"]", wc):
                    eager_loaded.add(rel)

            # 1. ->get() without pagination in list-like methods
            # Exclude dashboard/detail methods — they aggregate data, not list records
            method_lower = method_name.lower()
            is_list_method = (
                any(kw in method_lower for kw in ["index", "list", "search", "all", "export"])
                and not any(kw in method_lower for kw in ["dashboard", "show", "edit", "create", "store", "update", "destroy"])
                and "Dashboard" not in controller
            )
            if is_list_method:
                for i, line in enumerate(method_lines):
                    stripped = line.strip()
                    if re.search(r'->get\(\s*\)', stripped) and "paginate" not in method_body:
                        issues.append({
                            "method": method_name,
                            "line": start_line + i,
                            "severity": "error",
                            "code": "no-pagination",
                            "message": "->get() in list endpoint without pagination — use paginate()",
                            "snippet": stripped[:120],
                        })

            # 2. N+1: relationship access without with()
            seen_n1: Set[str] = set()
            for i, line in enumerate(method_lines):
                stripped = line.strip()
                for rel_match in re.finditer(r'\$\w+->([\w]+)', stripped):
                    rel_name = rel_match.group(1)
                    after_rel = stripped[rel_match.end():]
                    if after_rel.startswith("("):
                        continue  # method call, not property access

                    for model_name, rels in model_relationships.items():
                        if rel_name in rels and rel_name not in eager_loaded:
                            rel_type = rels[rel_name]
                            # Skip safe relationship types (belongsTo = single query)
                            if rel_type in _SAFE_RELATIONSHIP_TYPES:
                                continue
                            key = f"{model_name}.{rel_name}"
                            if key not in seen_n1:
                                seen_n1.add(key)
                                issues.append({
                                    "method": method_name,
                                    "line": start_line + i,
                                    "severity": "warning",
                                    "code": "n-plus-one",
                                    "message": f"'{rel_name}' ({rel_type}) accessed without with() — N+1 risk",
                                    "snippet": stripped[:120],
                                    "fix": f"Add ->with('{rel_name}') to the query",
                                })

            # 3. WHERE on non-indexed columns — resolve table per query chain
            self._check_where_indexes(
                method_body, method_name, start_line,
                imported_models, model_table_map, all_indexes, issues
            )

            # 4. ORDER BY on non-indexed columns
            self._check_orderby_indexes(
                method_body, method_name, start_line,
                imported_models, model_table_map, all_indexes, issues
            )

            # 5. Queries inside loops (proper brace-depth tracking)
            self._check_queries_in_loops(method_lines, method_name, start_line, issues)

            # 6. Missing ->select() on list methods
            if is_list_method:
                has_select = "->select(" in method_body or "->addSelect(" in method_body
                has_query = any(
                    pat in method_body
                    for pat in ["::where(", "::query(", "::active(", "->where("]
                )
                if has_query and not has_select:
                    issues.append({
                        "method": method_name,
                        "line": start_line,
                        "severity": "info",
                        "code": "no-select",
                        "message": "Query without ->select() — fetching all columns",
                        "fix": "Add ->select([...]) to fetch only needed columns",
                    })

        # Deduplicate issues (same code + same line)
        seen_issues: Set[str] = set()
        unique_issues: List[Dict[str, Any]] = []
        for iss in issues:
            key = f"{iss['code']}:{iss.get('line', 0)}:{iss.get('message', '')}"
            if key not in seen_issues:
                seen_issues.add(key)
                unique_issues.append(iss)

        errors = [i for i in unique_issues if i["severity"] == "error"]
        warnings = [i for i in unique_issues if i["severity"] == "warning"]
        infos = [i for i in unique_issues if i["severity"] == "info"]

        return {
            "status": "success",
            "file": rel_path,
            "controller": controller,
            "method": method or "all",
            "issues": unique_issues,
            "summary": {
                "errors": len(errors),
                "warnings": len(warnings),
                "info": len(infos),
                "total": len(unique_issues),
            },
            "models_analyzed": sorted(imported_models),
            "model_table_map": model_table_map,
            "eager_loaded_relationships": sorted(eager_loaded) if methods_to_check else [],
        }

    # ── rename_symbol ────────────────────────────────────────────────

    def rename_symbol(
        self, file_path: str, old_name: str, new_name: str, dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Find all references to a symbol and generate rename edits across files.

        Uses word-boundary matching to avoid false positives (e.g., renaming
        "active" won't match "is_active" or "activeCount").

        Args:
            file_path: File containing the symbol (e.g., "app/Models/User.php")
            old_name: Current symbol name
            new_name: New symbol name
            dry_run: If True (default), preview only. If False, apply with syntax check.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        intel = self._get_generic_intel()

        # Extract class name for context filtering
        content = self._read_file(full_path)
        class_name = None
        if content:
            class_match = re.search(r"class\s+(\w+)", content)
            if class_match:
                class_name = class_match.group(1)

        refs_result = intel.find_references(old_name, scope="all", context_filter=class_name)
        # find_references returns {"symbol": ..., "references": [...]} without "status" key
        # Only bail if there's an explicit error status
        if refs_result.get("status") == "error":
            return refs_result

        references = refs_result.get("references", [])

        # Build word-boundary regex for safe replacement
        # This prevents "published" matching inside "unpublished" or enum string values
        boundary_pattern = re.compile(
            r'(?<![a-zA-Z_])' + re.escape(old_name) + r'(?![a-zA-Z_\d])'
        )

        # Collect affected files
        affected_files: Set[str] = {file_path}  # always include source
        for ref in references:
            ref_file = ref.get("file", "")
            if ref_file:
                affected_files.add(ref_file)

        # Build rename plan with word-boundary counts
        rename_plan: List[Dict[str, Any]] = []
        skipped_files: List[Dict[str, Any]] = []

        for fpath in sorted(affected_files):
            full = os.path.join(base, fpath)
            if not os.path.isfile(full):
                continue

            file_content = self._read_file(full)
            if not file_content:
                continue

            # Count word-boundary matches (safe replacements)
            safe_count = len(boundary_pattern.findall(file_content))
            # Count raw string matches (total, including unsafe)
            raw_count = file_content.count(old_name)

            if safe_count == 0:
                if raw_count > 0:
                    skipped_files.append({
                        "file": fpath,
                        "raw_matches": raw_count,
                        "reason": "All matches are partial (inside other identifiers/strings)",
                    })
                continue

            unsafe_count = raw_count - safe_count

            entry: Dict[str, Any] = {
                "file": fpath,
                "safe_replacements": safe_count,
                "edit": {"search": old_name, "replace": new_name, "word_boundary": True},
            }
            if unsafe_count > 0:
                entry["skipped_partial_matches"] = unsafe_count
                entry["warning"] = f"{unsafe_count} partial match(es) will NOT be renamed"

            rename_plan.append(entry)

        if not rename_plan:
            return {
                "status": "success",
                "message": f"No safe references found for '{old_name}'",
                "files_affected": 0,
                "edits": [],
                "skipped_files": skipped_files,
            }

        result: Dict[str, Any] = {
            "status": "success",
            "old_name": old_name,
            "new_name": new_name,
            "dry_run": dry_run,
            "files_affected": len(rename_plan),
            "total_safe_replacements": sum(e["safe_replacements"] for e in rename_plan),
            "edits": rename_plan,
            "skipped_files": skipped_files,
        }

        if not dry_run:
            from .file_edit_service import FileEditService
            edit_svc = FileEditService(self.ctx)
            applied: List[Dict[str, Any]] = []
            failed: List[Dict[str, Any]] = []

            for plan in rename_plan:
                try:
                    fpath = plan["file"]
                    full = os.path.join(base, fpath)
                    file_content = self._read_file(full)
                    if not file_content:
                        failed.append({"file": fpath, "error": "Could not read file"})
                        continue

                    # Word-boundary replace
                    new_content = boundary_pattern.sub(new_name, file_content)
                    if new_content == file_content:
                        continue

                    # Write and syntax check
                    with open(full, "w", encoding="utf-8") as f:
                        f.write(new_content)

                    if fpath.endswith(".php"):
                        syntax_err = edit_svc._check_php_syntax(full)
                        if syntax_err:
                            with open(full, "w", encoding="utf-8") as f:
                                f.write(file_content)
                            failed.append({
                                "file": fpath,
                                "error": f"PHP syntax error after rename: {syntax_err}",
                            })
                            continue

                    replaced = plan["safe_replacements"]
                    applied.append({"file": fpath, "replacements": replaced})
                except Exception as e:
                    failed.append({"file": plan["file"], "error": str(e)})

            result["applied"] = applied
            result["failed"] = failed

        return result

    # ── check_eager_loading ──────────────────────────────────────────

    def check_eager_loading(self, file_path: str) -> Dict[str, Any]:
        """
        Audit a controller or Blade view for N+1 query risks.

        Severity-aware: hasMany/belongsToMany = warning (real N+1 danger),
        belongsTo/morphTo = info only (single query, rarely a problem).

        Also checks Blade views rendered by the controller.

        Args:
            file_path: Relative path to controller or Blade file
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file(full_path)
        if not content:
            return {"status": "error", "message": f"Could not read: {file_path}"}

        intel = self._get_intel()
        issues: List[Dict[str, Any]] = []
        is_blade = file_path.endswith(".blade.php")
        is_controller = "Controller" in file_path and file_path.endswith(".php")

        # Find which models are involved
        if is_controller:
            imported_models = self._find_imported_models(content)
        else:
            imported_models: Set[str] = set()
            try:
                blade_deps = intel.get_blade_dependencies(file_path)
                if blade_deps.get("status") == "success":
                    ctrl = blade_deps.get("rendered_by", {})
                    ctrl_file = ctrl.get("file", "")
                    if ctrl_file:
                        ctrl_content = self._read_file(os.path.join(base, ctrl_file))
                        if ctrl_content:
                            imported_models = self._find_imported_models(ctrl_content)
            except Exception:
                pass

        # Build relationship map with types
        all_relationships: Dict[str, Dict[str, str]] = {}
        for model_name in imported_models:
            try:
                rel_data = intel.get_laravel_relationships(model_name)
                if rel_data.get("status") == "success":
                    models = rel_data.get("models", {})
                    if model_name in models:
                        rels = models[model_name].get("relationships", [])
                        all_relationships[model_name] = {
                            r.get("name", ""): r.get("type", "")
                            for r in rels
                        }
            except Exception:
                pass

        # Find all with() and withCount() calls
        eager_loaded: Set[str] = set()
        for wc in re.findall(r"->with\(\s*\[?\s*([^)]+)\s*\]?\s*\)", content):
            for rel in re.findall(r"['\"](\w+)['\"]", wc):
                eager_loaded.add(rel)
        for wc in re.findall(r"->withCount\(\s*\[?\s*([^)]+)\s*\]?\s*\)", content):
            for rel in re.findall(r"['\"](\w+)['\"]", wc):
                eager_loaded.add(rel)
        # Also check load() / loadMissing()
        for wc in re.findall(r"->(?:load|loadMissing)\(\s*\[?\s*([^)]+)\s*\]?\s*\)", content):
            for rel in re.findall(r"['\"](\w+)['\"]", wc):
                eager_loaded.add(rel)

        # Scan for relationship property access
        lines = content.split("\n")
        seen_warnings: Set[str] = set()

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            for match in re.finditer(r'\$(\w+)->([\w]+)', stripped):
                prop_name = match.group(2)
                after = stripped[match.end():]
                if after.startswith("("):
                    continue  # method call

                for model_name, rels in all_relationships.items():
                    if prop_name in rels and prop_name not in eager_loaded:
                        warning_key = f"{model_name}.{prop_name}"
                        if warning_key in seen_warnings:
                            continue
                        seen_warnings.add(warning_key)
                        rel_type = rels[prop_name]
                        # belongsTo = info (single query), hasMany/belongsToMany = warning (N+1)
                        if rel_type in _SAFE_RELATIONSHIP_TYPES:
                            severity = "info"
                            msg_suffix = " (single query, low risk)"
                        else:
                            severity = "warning"
                            msg_suffix = " — N+1 risk in loops"
                        issues.append({
                            "line": i,
                            "severity": severity,
                            "code": "n-plus-one",
                            "model": model_name,
                            "relationship": prop_name,
                            "relationship_type": rel_type,
                            "message": f"'{prop_name}' ({rel_type}) accessed without eager loading{msg_suffix}",
                            "snippet": stripped[:120],
                            "fix": f"Add ->with('{prop_name}') to the query",
                        })

        # Check Blade views rendered by this controller
        blade_issues: List[Dict[str, Any]] = []
        if is_controller:
            # Build variable -> model mapping from controller context
            # e.g., Provider::... → $providers/$provider → Provider model
            var_model_map: Dict[str, str] = {}
            for model_name in imported_models:
                lower = model_name[0].lower() + model_name[1:]
                snake = re.sub(r'(?<!^)(?=[A-Z])', '_', model_name).lower()
                # Map both singular and plural variable names to the model
                for vname in [lower, snake, f"{lower}s", f"{snake}s"]:
                    var_model_map[vname] = model_name

            view_names = re.findall(r"view\(\s*['\"]([^'\"]+)['\"]", content)
            for view_name in view_names[:5]:
                view_path = os.path.join(
                    base, "resources", "views",
                    view_name.replace(".", "/") + ".blade.php"
                )
                if os.path.isfile(view_path):
                    blade_content = self._read_file(view_path)
                    if blade_content:
                        blade_lines = blade_content.split("\n")

                        # Track @foreach variable mappings:
                        # @foreach($providers as $provider) → $provider maps to Provider
                        foreach_var_map: Dict[str, str] = {}
                        for bline in blade_lines:
                            fm = re.search(
                                r'@foreach\s*\(\s*\$(\w+)\s+as\s+(?:\$\w+\s*=>\s*)?\$(\w+)',
                                bline
                            )
                            if fm:
                                collection_var = fm.group(1)
                                item_var = fm.group(2)
                                # If collection var maps to a model, the item var does too
                                if collection_var in var_model_map:
                                    foreach_var_map[item_var] = var_model_map[collection_var]

                        for j, bline in enumerate(blade_lines, 1):
                            bstripped = bline.strip()
                            for match in re.finditer(r'\$(\w+)->([\w]+)', bstripped):
                                var_name = match.group(1)
                                prop = match.group(2)
                                after = bstripped[match.end():]
                                if after.startswith("("):
                                    continue

                                # Determine which model this variable belongs to
                                target_model = (
                                    var_model_map.get(var_name)
                                    or foreach_var_map.get(var_name)
                                )

                                if target_model and target_model in all_relationships:
                                    rels = all_relationships[target_model]
                                    if prop in rels and prop not in eager_loaded:
                                        key = f"blade.{view_name}.{prop}"
                                        if key in seen_warnings:
                                            continue
                                        seen_warnings.add(key)
                                        rel_type = rels[prop]
                                        if rel_type in _SAFE_RELATIONSHIP_TYPES:
                                            severity = "info"
                                        else:
                                            severity = "warning"
                                        blade_issues.append({
                                            "view": view_name,
                                            "line": j,
                                            "severity": severity,
                                            "model": target_model,
                                            "relationship": prop,
                                            "relationship_type": rel_type,
                                            "variable": f"${var_name}",
                                            "message": f"${var_name}->{prop} ({rel_type}) in Blade without eager loading",
                                            "fix": f"Add ->with('{prop}') in controller query",
                                        })
                                    continue

                                # Fallback: check all models (original behavior)
                                for model_name, rels in all_relationships.items():
                                    if prop in rels and prop not in eager_loaded:
                                        key = f"blade.{view_name}.{prop}"
                                        if key in seen_warnings:
                                            continue
                                        seen_warnings.add(key)
                                        rel_type = rels[prop]
                                        if rel_type in _SAFE_RELATIONSHIP_TYPES:
                                            severity = "info"
                                        else:
                                            severity = "warning"
                                        blade_issues.append({
                                            "view": view_name,
                                            "line": j,
                                            "severity": severity,
                                            "model": model_name,
                                            "relationship": prop,
                                            "relationship_type": rel_type,
                                            "message": f"'{prop}' accessed in Blade without eager loading in controller",
                                            "fix": f"Add ->with('{prop}') in controller query",
                                        })

        return {
            "status": "success",
            "file": file_path,
            "file_type": "blade" if is_blade else "controller",
            "eager_loaded": sorted(eager_loaded),
            "models_analyzed": sorted(imported_models),
            "issues": issues,
            "blade_issues": blade_issues,
            "summary": {
                "n_plus_one_risks": len([i for i in issues if i["severity"] == "warning"])
                                    + len([i for i in blade_issues if i.get("severity") == "warning"]),
                "info_items": len([i for i in issues if i["severity"] == "info"])
                              + len([i for i in blade_issues if i.get("severity") == "info"]),
                "eager_loaded_count": len(eager_loaded),
                "models_checked": len(imported_models),
            },
        }

    # ── auto_anti_pattern_check ──────────────────────────────────────

    def auto_anti_pattern_check(self, file_path: str) -> Dict[str, Any]:
        """
        Run detect_anti_patterns automatically after an edit.

        Designed to be called after apply_edit as a post-edit hook.
        Returns compact output for inline feedback.

        Args:
            file_path: Relative path of the file that was just edited
        """
        validator = self._get_validator()
        result = validator.detect_anti_patterns(file_path)

        if result.get("status") != "success":
            return result

        issues = result.get("issues", [])

        if not issues:
            return {
                "status": "clean",
                "file": file_path,
                "message": "No anti-patterns detected",
            }

        return {
            "status": "issues_found",
            "file": file_path,
            "issue_count": len(issues),
            "errors": [
                {"line": i["line"], "code": i["code"], "message": i["message"],
                 "snippet": i.get("snippet", "")}
                for i in issues if i["severity"] == "error"
            ],
            "warnings": [
                {"line": i["line"], "code": i["code"], "message": i["message"]}
                for i in issues if i["severity"] == "warning"
            ],
            "info_count": sum(1 for i in issues if i["severity"] == "info"),
        }

    # ── detect_cache_conflicts ───────────────────────────────────────

    def detect_cache_conflicts(self, cache_key: str = None) -> Dict[str, Any]:
        """
        Detect cache key conflicts and inconsistencies.

        Independently scans ALL PHP files for Cache::remember/get readers,
        not just relying on get_cache_map's reverse_map (which only has invalidators).

        Args:
            cache_key: Optional cache key pattern to filter results.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        validator = self._get_validator()
        cache_map_result = validator.get_cache_map()

        if cache_map_result.get("status") != "success":
            return cache_map_result

        forward_map = cache_map_result.get("forward_map", {})

        # Build writer map: cache_key -> [models that invalidate it]
        # Normalize variable interpolations to {$var} for consistent matching with readers
        all_writers: Dict[str, List[str]] = {}
        for model_name, model_data in forward_map.items():
            invalidates = model_data.get("invalidates", []) if isinstance(model_data, dict) else []
            for key_info in invalidates:
                key_str = key_info.get("key", "") if isinstance(key_info, dict) else str(key_info)
                if key_str:
                    # Normalize dynamic keys: {$post->id} → {$var}, {$i} → {$var}
                    normalized = re.sub(r'\{\$[\w>?-]+\}', '{$var}', key_str)
                    all_writers.setdefault(normalized, []).append(model_name)

        # INDEPENDENTLY scan for Cache readers across all PHP files
        all_readers: Dict[str, List[str]] = {}
        scan_dirs = [
            os.path.join(base, "app", "Http", "Controllers"),
            os.path.join(base, "app", "Services"),
            os.path.join(base, "app", "Models"),
        ]

        # Track which (file, position) combos we've already recorded to avoid
        # the same Cache call being matched by multiple regex patterns
        seen_reader_hits: Set[Tuple[str, int]] = set()

        for scan_dir in scan_dirs:
            if not os.path.isdir(scan_dir):
                continue
            for root, _dirs, files in os.walk(scan_dir):
                for fname in files:
                    if not fname.endswith(".php"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        fcontent = open(fpath, "r", encoding="utf-8", errors="replace").read()
                    except Exception:
                        continue

                    rel = os.path.relpath(fpath, base)

                    # Unified regex: match both single and double quoted Cache reads
                    cache_read_re = re.compile(
                        r"Cache::(?:remember|rememberForever|get|has|pull)\(\s*"
                        r"(?:"
                        r"'([^']+)'"          # group 1: single-quoted key
                        r"|"
                        r'"([^"]+)"'          # group 2: double-quoted key
                        r")"
                    )

                    for m in cache_read_re.finditer(fcontent):
                        hit_key = (rel, m.start())
                        if hit_key in seen_reader_hits:
                            continue
                        seen_reader_hits.add(hit_key)

                        raw_key = m.group(1) or m.group(2) or ""
                        if not raw_key:
                            continue

                        # Normalize all variable interpolations to {$var}
                        normalized = re.sub(r'\{\$[\w>?-]+\}', '{$var}', raw_key)
                        normalized = re.sub(r'\$[\w>?-]+', '{$var}', normalized)
                        all_readers.setdefault(normalized, []).append(rel)

                    # Also handle concat pattern: Cache::remember('key.' . $var, ...)
                    for m in re.finditer(
                        r"Cache::(?:remember|rememberForever|get|has|pull)\(\s*'([^']+)'\s*\.\s*\$",
                        fcontent
                    ):
                        hit_key = (rel, m.start())
                        if hit_key in seen_reader_hits:
                            continue
                        seen_reader_hits.add(hit_key)

                        prefix = m.group(1)
                        all_readers.setdefault(f"{prefix}{{$var}}", []).append(rel)

        conflicts: List[Dict[str, Any]] = []
        dead_keys: List[Dict[str, Any]] = []
        stale_risks: List[Dict[str, Any]] = []
        pattern_conflicts: List[Dict[str, Any]] = []

        # Deduplicate reader lists
        for k in all_readers:
            all_readers[k] = sorted(set(all_readers[k]))

        all_write_keys = set(all_writers.keys())
        all_read_keys = set(all_readers.keys())

        # 1. Duplicate writers
        for key, writers in all_writers.items():
            unique_writers = sorted(set(writers))
            if len(unique_writers) > 1:
                conflicts.append({
                    "cache_key": key,
                    "severity": "warning",
                    "message": f"Cache key '{key}' invalidated by multiple models",
                    "models": unique_writers,
                })

        # 2. Dead cache — invalidated but never read
        for key in all_write_keys:
            if key not in all_read_keys:
                if not any(self._cache_key_matches(key, rk) for rk in all_read_keys):
                    if not any(self._cache_key_matches(rk, key) for rk in all_read_keys):
                        dead_keys.append({
                            "cache_key": key,
                            "severity": "info",
                            "message": f"Cache key '{key}' is invalidated but never read",
                            "invalidated_by": sorted(set(all_writers[key])),
                        })

        # 3. Stale risk — read but never invalidated
        for key in all_read_keys:
            if key not in all_write_keys:
                if not any(self._cache_key_matches(wk, key) for wk in all_write_keys):
                    if not any(self._cache_key_matches(key, wk) for wk in all_write_keys):
                        stale_risks.append({
                            "cache_key": key,
                            "severity": "warning",
                            "message": f"Cache key '{key}' is read but never explicitly invalidated",
                            "read_by": all_readers.get(key, []),
                        })

        # 4. Pattern/wildcard conflicts
        wildcard_keys = [k for k in all_write_keys if "*" in k or "{" in k]
        static_keys = [k for k in all_write_keys if "*" not in k and "{" not in k]
        for wk in wildcard_keys:
            base_prefix = wk.split("*")[0].split("{")[0]
            for sk in static_keys:
                if sk.startswith(base_prefix) and sk != wk:
                    pattern_conflicts.append({
                        "wildcard_key": wk,
                        "static_key": sk,
                        "severity": "info",
                        "message": f"Wildcard '{wk}' might overlap with static key '{sk}'",
                    })

        # Filter by specific key if requested
        if cache_key:
            conflicts = [c for c in conflicts if cache_key in c.get("cache_key", "")]
            dead_keys = [d for d in dead_keys if cache_key in d.get("cache_key", "")]
            stale_risks = [s for s in stale_risks if cache_key in s.get("cache_key", "")]
            pattern_conflicts = [
                p for p in pattern_conflicts
                if cache_key in p.get("wildcard_key", "") or cache_key in p.get("static_key", "")
            ]

        return {
            "status": "success",
            "filter": cache_key,
            "total_write_keys": len(all_write_keys),
            "total_read_keys": len(all_read_keys),
            "conflicts": conflicts,
            "dead_keys": dead_keys,
            "stale_risks": stale_risks,
            "pattern_conflicts": pattern_conflicts,
            "summary": {
                "conflicts": len(conflicts),
                "dead_keys": len(dead_keys),
                "stale_risks": len(stale_risks),
                "pattern_conflicts": len(pattern_conflicts),
                "total_issues": len(conflicts) + len(dead_keys) + len(stale_risks) + len(pattern_conflicts),
            },
        }

    # ── diff_preview ─────────────────────────────────────────────────

    def diff_preview(self, edits: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Preview multi-file edits without applying them (dry-run).

        Args:
            edits: List of edit specs. Each: {"file_path": str, "search": str, "replace": str}
        """
        import difflib

        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        if not edits:
            return {"status": "error", "message": "No edits provided"}

        previews: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        total_additions = 0
        total_deletions = 0

        for edit in edits:
            file_path = edit.get("file_path", "")
            search = edit.get("search", "")
            replace = edit.get("replace", "")

            if not file_path or not search:
                errors.append({"file": file_path, "error": "Missing file_path or search"})
                continue

            full_path = os.path.join(base, file_path)
            if not os.path.isfile(full_path):
                errors.append({"file": file_path, "error": "File not found"})
                continue

            content = self._read_file(full_path)
            if content is None:
                errors.append({"file": file_path, "error": "Could not read file"})
                continue

            occurrences = content.count(search)
            if occurrences == 0:
                errors.append({
                    "file": file_path,
                    "error": "Search string not found in file",
                    "search_preview": search[:80],
                })
                continue

            new_content = content.replace(search, replace, 1)

            diff_lines = list(difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                n=3,
            ))

            additions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
            deletions = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
            total_additions += additions
            total_deletions += deletions

            diff_text = "".join(diff_lines)
            if len(diff_lines) > 50:
                diff_text = "".join(diff_lines[:50]) + f"\n... ({len(diff_lines) - 50} more lines)"

            previews.append({
                "file": file_path,
                "occurrences": occurrences,
                "additions": additions,
                "deletions": deletions,
                "diff": diff_text,
            })

        return {
            "status": "success",
            "dry_run": True,
            "previews": previews,
            "errors": errors,
            "summary": {
                "files_affected": len(previews),
                "files_with_errors": len(errors),
                "total_additions": total_additions,
                "total_deletions": total_deletions,
            },
        }

    # ── get_context_for_edit ────────────────────────────────────────

    def get_context_for_edit(self, file_path: str, symbol: str = None) -> Dict[str, Any]:
        """
        Auto-gather ALL context needed before editing a file/symbol.

        This is the "external brain" tool — the model calls this ONCE and gets
        everything it needs to write correct code, without reading any files
        into its own context window.

        Returns (based on file type):
        - Model file: relationships, scopes, fillable, table schema, cache keys,
          affected controllers/views, traits
        - Controller file: route info, middleware, form request rules, rendered views,
          imported models with relationships, service calls
        - Service file: callers (which controllers use this), model dependencies
        - Blade file: parent layout, controller that renders it, passed variables,
          components used, Alpine.js data
        - Any PHP file: class hierarchy, who imports this, anti-pattern scan

        Args:
            file_path: Relative path to the file about to be edited
            symbol: Optional specific method/property to focus on
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        intel = self._get_intel()
        validator = self._get_validator()
        context: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "symbol": symbol,
        }

        # Determine file type
        is_model = "app/Models/" in file_path
        is_controller = "Controller" in file_path and "app/Http/Controllers/" in file_path
        is_service = "app/Services/" in file_path
        is_blade = file_path.endswith(".blade.php")
        is_request = "app/Http/Requests/" in file_path
        is_middleware = "app/Http/Middleware/" in file_path

        context["file_type"] = (
            "model" if is_model else
            "controller" if is_controller else
            "service" if is_service else
            "blade" if is_blade else
            "form_request" if is_request else
            "middleware" if is_middleware else
            "php"
        )

        # ── Symbol code + ripple effect (if symbol specified) ──
        if symbol:
            # Show the symbol's actual code so model doesn't need to Read the file
            try:
                from .code_intelligence_service import CodeIntelligenceService
                code_svc = CodeIntelligenceService(self.ctx)
                sym_result = code_svc.get_symbol_body(file_path, symbol)
                if sym_result and sym_result.get("status") != "error":
                    context["symbol_code"] = {
                        "code": sym_result.get("code", ""),
                        "start_line": sym_result.get("start_line"),
                        "end_line": sym_result.get("end_line"),
                        "signature": sym_result.get("signature", ""),
                    }
            except Exception:
                pass

            # Ripple effect
            try:
                ripple = intel.get_ripple_effect(file_path, symbol)
                if ripple.get("status") == "success":
                    summary = ripple.get("summary", {})
                    symbol_info = ripple.get("symbol_info", {})

                    # Collect ALL affected files from all impact categories
                    all_affected: List[Dict[str, Any]] = []
                    for impact in ripple.get("direct_impacts", []):
                        all_affected.append(impact)
                    for impact in ripple.get("indirect_impacts", []):
                        all_affected.append(impact)

                    # Deduplicate by file
                    seen_files: Set[str] = set()
                    unique_affected: List[str] = []
                    for imp in all_affected:
                        f = imp.get("file", "")
                        if f and f != file_path and f not in seen_files:
                            seen_files.add(f)
                            unique_affected.append(f)

                    # Cache keys — try both "key" and "cache_key" field names
                    cache_keys: List[str] = []
                    for c in ripple.get("cache_impacts", [])[:15]:
                        k = c.get("cache_key") or c.get("key") or ""
                        if k and k not in cache_keys:
                            cache_keys.append(k)

                    ripple_data: Dict[str, Any] = {
                        "risk_level": summary.get("risk_level", "unknown"),
                        "total_files_affected": summary.get("total_files_affected", 0),
                        "affected_files": unique_affected[:15],
                        "views_affected": [
                            v.get("file") for v in ripple.get("view_impacts", [])[:5]
                            if v.get("file")
                        ],
                        "cache_keys_affected": cache_keys,
                    }

                    if symbol_info:
                        ripple_data["symbol_info"] = symbol_info

                    # Show code snippets from affected files
                    # Use the scope name for searching (e.g., "active" not "scopeActive")
                    search_term = symbol
                    if symbol_info.get("is_scope") and symbol_info.get("scope_name"):
                        search_term = symbol_info["scope_name"]

                    affected_code: List[Dict[str, Any]] = []
                    for imp in all_affected[:8]:
                        imp_file = imp.get("file", "")
                        if not imp_file or imp_file == file_path:
                            continue
                        # Use the text from ripple if available
                        imp_text = imp.get("text", "")
                        imp_line = imp.get("line")
                        if imp_text and imp_line:
                            # We have the line from ripple — use it directly
                            file_key = imp_file
                            existing = next((a for a in affected_code if a["file"] == file_key), None)
                            if existing:
                                if len(existing["lines"]) < 5:
                                    existing["lines"].append({
                                        "line": imp_line,
                                        "code": imp_text.strip()[:150],
                                    })
                            else:
                                affected_code.append({
                                    "file": imp_file,
                                    "lines": [{
                                        "line": imp_line,
                                        "code": imp_text.strip()[:150],
                                    }],
                                })
                        elif imp_file not in [a["file"] for a in affected_code]:
                            # Fallback: read file and search
                            imp_full = os.path.join(base, imp_file)
                            if os.path.isfile(imp_full):
                                imp_content = self._read_file(imp_full)
                                if imp_content:
                                    lines_found = []
                                    for ln, line in enumerate(imp_content.split("\n"), 1):
                                        if search_term in line:
                                            lines_found.append({
                                                "line": ln,
                                                "code": line.strip()[:150],
                                            })
                                    if lines_found:
                                        affected_code.append({
                                            "file": imp_file,
                                            "lines": lines_found[:5],
                                        })

                    if affected_code:
                        ripple_data["affected_files_code"] = affected_code

                    context["ripple_effect"] = ripple_data
            except Exception:
                pass

        # ── Model-specific context ──
        if is_model:
            content_str = self._read_file(full_path)
            class_match = re.search(r"class\s+(\w+)", content_str) if content_str else None
            model_name = class_match.group(1) if class_match else None

            if model_name:
                # Relationships + fillable
                try:
                    rel_data = intel.get_laravel_relationships(model_name)
                    if rel_data.get("status") == "success":
                        model_info = rel_data.get("models", {}).get(model_name, {})
                        context["relationships"] = model_info.get("relationships", [])
                        context["fillable"] = model_info.get("fillable", [])
                        context["traits"] = model_info.get("traits", [])
                except Exception:
                    pass

                # Scopes — extract from model file directly since
                # get_laravel_relationships doesn't return scopes
                if content_str:
                    scopes = []
                    for scope_match in re.finditer(
                        r'public\s+function\s+(scope\w+)\s*\(', content_str
                    ):
                        scope_name = scope_match.group(1)
                        # Extract usage name: scopeActive → active()
                        usage = scope_name[5].lower() + scope_name[6:] if len(scope_name) > 5 else scope_name
                        scopes.append({"method": scope_name, "usage": usage})
                    context["scopes"] = scopes

                # Table schema
                try:
                    table = self._build_model_table_map(base, {model_name}).get(model_name)
                    if table:
                        schema = intel.get_migration_schema(table)
                        if schema.get("status") == "success":
                            # get_migration_schema returns flat format when table is specified:
                            # {"table": "x", "columns": [...], "indexes": [...]}
                            # OR nested: {"tables": {"x": {"columns": [...]}}}
                            if "tables" in schema:
                                table_data = schema["tables"].get(table, {})
                                cols = table_data.get("columns", [])
                                idxs = table_data.get("indexes", [])
                            else:
                                cols = schema.get("columns", [])
                                idxs = schema.get("indexes", [])

                            context["table"] = table
                            context["columns"] = [
                                {"name": c.get("name", ""), "type": c.get("type", ""),
                                 "nullable": c.get("nullable", False)}
                                for c in cols if c.get("name") and not c.get("dropped")
                            ]
                            context["indexes"] = idxs
                except Exception:
                    pass

                # Cache keys this model invalidates
                try:
                    cache_data = validator.get_cache_map(model_name)
                    if cache_data.get("status") == "success":
                        fwd = cache_data.get("forward_map", {}).get(model_name, {})
                        context["cache_invalidates"] = [
                            k.get("key") for k in fwd.get("invalidates", [])
                        ] if isinstance(fwd, dict) else []
                except Exception:
                    pass

                # Who uses this model (controllers)
                try:
                    refs = intel.find_references(model_name, scope="controllers")
                    files = [r.get("file") for r in refs.get("references", [])]
                    context["used_by_controllers"] = files[:10]
                except Exception:
                    pass

        # ── Controller-specific context ──
        elif is_controller:
            if symbol:
                # Flow map for the specific method
                try:
                    content_str = self._read_file(full_path)
                    class_match = re.search(r"class\s+(\w+)", content_str) if content_str else None
                    ctrl_name = class_match.group(1) if class_match else None
                    if ctrl_name:
                        flow = intel.get_flow_map(ctrl_name, method=symbol)
                        if flow.get("status") == "success":
                            flows = flow.get("flows", [])
                            if flows:
                                f = flows[0] if isinstance(flows, list) else flows
                                context["route"] = f.get("route", {})
                                context["middleware"] = f.get("middleware", [])
                                context["validation"] = f.get("validation", {})
                                context["views_rendered"] = f.get("views", [])
                except Exception:
                    pass

            # Imported models with their relationships
            content_str = content_str if 'content_str' in dir() else self._read_file(full_path)
            if content_str:
                imported = self._find_imported_models(content_str)
                models_context: Dict[str, Any] = {}
                for m in list(imported)[:8]:  # Cap at 8 models
                    try:
                        rel_data = intel.get_laravel_relationships(m)
                        if rel_data.get("status") == "success":
                            mi = rel_data.get("models", {}).get(m, {})
                            models_context[m] = {
                                "relationships": [r["name"] for r in mi.get("relationships", [])],
                                "scopes": [s["name"] if isinstance(s, dict) else s for s in mi.get("scopes", [])],
                            }
                    except Exception:
                        pass
                if models_context:
                    context["imported_models"] = models_context

        # ── Service-specific context ──
        elif is_service:
            content_str = self._read_file(full_path)
            class_match = re.search(r"class\s+(\w+)", content_str) if content_str else None
            svc_name = class_match.group(1) if class_match else None
            if svc_name:
                try:
                    refs = intel.find_references(svc_name, scope="controllers")
                    context["called_by"] = [
                        r.get("file") for r in refs.get("references", [])
                    ][:10]
                except Exception:
                    pass

        # ── Blade-specific context ──
        elif is_blade:
            try:
                deps = intel.get_blade_dependencies(file_path)
                if deps.get("status") == "success":
                    context["layout"] = deps.get("parent_layout")
                    context["rendered_by"] = deps.get("rendered_by", {})
                    context["components_used"] = deps.get("components", [])
                    context["sections"] = deps.get("sections", [])
                    context["alpine_components"] = deps.get("alpine_components", [])
            except Exception:
                pass

            # View data flow — what variables are passed
            try:
                vdf = validator.get_view_data_flow(file_path)
                if vdf.get("status") == "success":
                    context["passed_variables"] = vdf.get("controller_data", [])
                    context["potentially_undefined"] = vdf.get("potentially_undefined", [])
            except Exception:
                pass

        # ── Class hierarchy (all PHP files) ──
        if file_path.endswith(".php") and not is_blade:
            content_str = self._read_file(full_path) if 'content_str' not in dir() else content_str
            if content_str:
                class_match = re.search(r"class\s+(\w+)", content_str)
                if class_match:
                    try:
                        hierarchy = intel.get_class_hierarchy(class_match.group(1))
                        if hierarchy.get("status") == "success":
                            context["extends"] = hierarchy.get("extends")
                            context["implements"] = hierarchy.get("implements", [])
                            context["uses_traits"] = hierarchy.get("traits", [])
                    except Exception:
                        pass

        return context

    # ── smart_apply_edit ─────────────────────────────────────────────

    def smart_apply_edit(
        self,
        file_path: str,
        search: str = None,
        replace: str = None,
        edits: list = None,
        symbol: str = None,
        new_code: str = None,
        start_line: int = None,
        end_line: int = None,
        occurrence: int = None,
    ) -> Dict[str, Any]:
        """
        The ultimate single-call edit tool. apply_edit on steroids.

        Does everything in ONE call:
        1. Applies the edit with PHP syntax check + auto-rollback
        2. Runs anti-pattern check (CLAUDE.md violations)
        3. Detects which symbols were changed
        4. Runs ripple effect on changed symbols
        5. Shows affected files' RELEVANT CODE SNIPPETS so you can fix them too
        6. Shows cache keys that need invalidation review

        The key innovation: when ripple effect finds affected files, it doesn't
        just list file names — it shows the EXACT lines in those files that
        reference the changed symbol, so you can update them without Read calls.

        Args: Same as apply_edit — all 5 modes supported.
        """
        from .file_edit_service import FileEditService

        edit_svc = FileEditService(self.ctx)
        intel = self._get_generic_intel()

        # Apply the edit via standard apply_edit
        result = edit_svc.apply_edit(
            file_path=file_path,
            search=search,
            replace=replace,
            symbol=symbol,
            new_code=new_code,
            edits=edits,
            start_line=start_line,
            end_line=end_line,
            occurrence=occurrence,
        )

        if result.get("status") != "success":
            return result

        base = self._get_project_path()
        if not base:
            return result

        # Only do deep analysis for high-risk files
        is_high_risk = any(
            p in file_path
            for p in ["app/Models/", "app/Services/", "app/Http/Controllers/",
                       "app/Http/Middleware/", "app/Http/Requests/"]
        )

        if not is_high_risk:
            return result

        # Detect what changed
        changed_symbols = self._detect_changed_symbols(search, replace, edits, symbol)

        if not changed_symbols:
            return result

        ripple_warnings: List[Dict[str, Any]] = []

        for sym in changed_symbols[:3]:
            try:
                ripple = intel.get_ripple_effect(file_path, sym)
                if ripple.get("status") != "success":
                    continue

                summary = ripple.get("summary", {})
                risk = summary.get("risk_level", "low")
                total = summary.get("total_files_affected", 0)

                if risk == "low" and total <= 2:
                    continue

                # Merge direct + indirect impacts (scopes appear in indirect)
                all_impacts: List[Dict[str, Any]] = []
                for imp in ripple.get("direct_impacts", []):
                    all_impacts.append(imp)
                for imp in ripple.get("indirect_impacts", []):
                    all_impacts.append(imp)

                # Determine search term (scope "active" vs method "scopeActive")
                sym_info = ripple.get("symbol_info", {})
                search_term = sym
                if sym_info.get("is_scope") and sym_info.get("scope_name"):
                    search_term = sym_info["scope_name"]

                # Collect affected files with code snippets
                affected_with_code: List[Dict[str, Any]] = []
                seen_aff: Set[str] = set()

                for impact in all_impacts[:10]:
                    imp_file = impact.get("file", "")
                    if not imp_file or imp_file == file_path or imp_file in seen_aff:
                        continue
                    seen_aff.add(imp_file)

                    # Use text from ripple if available
                    imp_text = impact.get("text", "")
                    imp_line = impact.get("line")

                    if imp_text and imp_line:
                        existing = next((a for a in affected_with_code if a["file"] == imp_file), None)
                        if existing:
                            if len(existing["lines"]) < 5:
                                existing["lines"].append({
                                    "line": imp_line,
                                    "code": imp_text.strip()[:150],
                                })
                                existing["usages"] += 1
                        else:
                            affected_with_code.append({
                                "file": imp_file,
                                "usages": 1,
                                "lines": [{"line": imp_line, "code": imp_text.strip()[:150]}],
                            })
                    else:
                        # Fallback: read file
                        imp_full = os.path.join(base, imp_file)
                        if not os.path.isfile(imp_full):
                            continue
                        imp_content = self._read_file(imp_full)
                        if not imp_content:
                            continue
                        relevant_lines = []
                        for line_num, line in enumerate(imp_content.split("\n"), 1):
                            if search_term in line:
                                relevant_lines.append({
                                    "line": line_num,
                                    "code": line.strip()[:150],
                                })
                        if relevant_lines:
                            affected_with_code.append({
                                "file": imp_file,
                                "usages": len(relevant_lines),
                                "lines": relevant_lines[:5],
                            })

                # View impacts with snippets
                view_impacts: List[Dict[str, Any]] = []
                for vi in ripple.get("view_impacts", [])[:3]:
                    vi_file = vi.get("file", "")
                    if vi_file:
                        vi_full = os.path.join(base, vi_file)
                        if os.path.isfile(vi_full):
                            vi_content = self._read_file(vi_full)
                            if vi_content:
                                vi_lines_with_sym = []
                                for ln, line in enumerate(vi_content.split("\n"), 1):
                                    if search_term in line:
                                        vi_lines_with_sym.append({
                                            "line": ln,
                                            "code": line.strip()[:120],
                                        })
                                if vi_lines_with_sym:
                                    view_impacts.append({
                                        "file": vi_file,
                                        "lines": vi_lines_with_sym[:3],
                                    })

                warning_entry: Dict[str, Any] = {
                    "symbol": sym,
                    "risk_level": risk,
                    "total_files_affected": total,
                }

                if affected_with_code:
                    warning_entry["affected_files_with_code"] = affected_with_code

                if view_impacts:
                    warning_entry["affected_views_with_code"] = view_impacts

                # Cache keys — try both field names
                cache_keys = []
                for c in ripple.get("cache_impacts", [])[:10]:
                    k = c.get("cache_key") or c.get("key") or ""
                    if k and k not in cache_keys:
                        cache_keys.append(k)
                if cache_keys:
                    warning_entry["cache_keys_to_review"] = cache_keys

                ripple_warnings.append(warning_entry)

            except Exception:
                pass

        if ripple_warnings:
            result["ripple_warnings"] = ripple_warnings

        return result

    @staticmethod
    def _detect_changed_symbols(
        search: str = None, replace: str = None,
        edits: list = None, symbol: str = None
    ) -> List[str]:
        """Detect which symbols were likely changed based on edit parameters."""
        symbols: List[str] = []

        if symbol:
            symbols.append(symbol)
            return symbols

        texts = []
        if search and replace:
            texts.append((search, replace))
        if edits:
            for e in edits:
                s = e.get("search", "")
                r = e.get("replace", "")
                if s and r:
                    texts.append((s, r))

        for old_text, new_text in texts:
            # Function names
            for m in re.finditer(r'function\s+(\w+)', old_text):
                symbols.append(m.group(1))
            for m in re.finditer(r'function\s+(\w+)', new_text):
                if m.group(1) not in symbols:
                    symbols.append(m.group(1))

            # Scope names
            for m in re.finditer(r'scope(\w+)', old_text):
                symbols.append(f"scope{m.group(1)}")

            # Property names
            for m in re.finditer(r'(?:public|protected|private)\s+(?:\?\w+\s+)?\$(\w+)', old_text):
                symbols.append(m.group(1))

            # Column/field names in where clauses or fillable changes
            for m in re.finditer(r"['\"](\w+)['\"]", old_text):
                word = m.group(1)
                # Only include if it also appears differently in new_text context
                if word not in new_text and len(word) > 2:
                    symbols.append(word)

        return list(dict.fromkeys(symbols))  # Deduplicate preserving order

    # ══════════════════════════════════════════════════════════════════
    # Helper Methods
    # ══════════════════════════════════════════════════════════════════

    def _find_controller_file(self, base: str, controller: str) -> Optional[str]:
        """Find a controller file by name, searching in subdirectories."""
        controllers_dir = os.path.join(base, "app", "Http", "Controllers")
        if not os.path.isdir(controllers_dir):
            return None

        if "/" in controller:
            parts = controller.split("/")
            filename = parts[-1]
            if not filename.endswith(".php"):
                filename += ".php"
            candidate = os.path.join(controllers_dir, *parts[:-1], filename)
            if os.path.isfile(candidate):
                return candidate
        else:
            filename = controller if controller.endswith(".php") else f"{controller}.php"
            for root, _dirs, files in os.walk(controllers_dir):
                if filename in files:
                    return os.path.join(root, filename)
        return None

    def _extract_methods(
        self, content: str, target_method: str = None
    ) -> List[Tuple[str, str, int]]:
        """Extract PHP methods. Returns [(method_name, method_body, start_line)]."""
        methods = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            match = re.match(
                r"\s*(?:public|protected|private)\s+(?:static\s+)?function\s+(\w+)\s*\(",
                line,
            )
            if not match:
                continue

            method_name = match.group(1)
            if target_method and method_name != target_method:
                continue

            brace_count = 0
            method_lines = []
            started = False

            for j in range(i, min(i + 500, len(lines))):
                method_lines.append(lines[j])
                brace_count += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and brace_count <= 0:
                    break

            methods.append((method_name, "\n".join(method_lines), i + 1))

        return methods

    def _find_imported_models(self, content: str) -> Set[str]:
        """Extract imported model names from PHP use statements."""
        models: Set[str] = set()
        for match in re.finditer(r"use\s+App\\Models\\(\w+)", content):
            models.add(match.group(1))
        return models

    def _build_model_table_map(self, base: str, models: Set[str]) -> Dict[str, str]:
        """
        Build model -> table name map by reading each model's $table property,
        falling back to Laravel's convention (pluralized snake_case).
        """
        result: Dict[str, str] = {}
        models_dir = os.path.join(base, "app", "Models")

        for model in models:
            model_file = os.path.join(models_dir, f"{model}.php")
            table = None

            if os.path.isfile(model_file):
                model_content = self._read_file(model_file)
                if model_content:
                    # Look for protected $table = 'table_name';
                    table_match = re.search(
                        r"protected\s+\$table\s*=\s*['\"](\w+)['\"]",
                        model_content,
                    )
                    if table_match:
                        table = table_match.group(1)

            if not table:
                table = self._model_to_table(model)

            result[model] = table

        return result

    @staticmethod
    def _model_to_table(model_name: str) -> str:
        """Convert ModelName to table_name following Laravel conventions."""
        # Check irregular plurals first
        if model_name in _IRREGULAR_PLURALS:
            return _IRREGULAR_PLURALS[model_name].lower()

        # snake_case conversion
        table = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", model_name).lower()

        # Basic English pluralization rules
        if table.endswith("y") and not table.endswith(("ay", "ey", "oy", "uy")):
            table = table[:-1] + "ies"
        elif table.endswith(("s", "sh", "ch", "x", "z")):
            table += "es"
        elif not table.endswith("s"):
            table += "s"

        return table

    def _extract_all_indexes(self, schema_data: Dict[str, Any]) -> Dict[str, Set[str]]:
        """Extract indexed columns per table from migration schema data."""
        indexes: Dict[str, Set[str]] = {}

        if schema_data.get("status") != "success":
            return indexes

        tables = schema_data.get("tables", {})
        for table_name, table_data in tables.items():
            indexed_cols: Set[str] = {"id"}  # PK always indexed

            for idx in table_data.get("indexes", []):
                for col in idx.get("columns", []):
                    indexed_cols.add(col)

            for fk in table_data.get("foreign_keys", []):
                col = fk.get("column", "")
                if col:
                    indexed_cols.add(col)

            for col_data in table_data.get("columns", []):
                if col_data.get("unique"):
                    indexed_cols.add(col_data.get("name", ""))

            indexes[table_name] = indexed_cols

        return indexes

    def _resolve_table_for_query(
        self, query_context: str, imported_models: Set[str],
        model_table_map: Dict[str, str]
    ) -> Optional[str]:
        """
        Resolve the table name for a specific query chain.

        Looks for Model:: prefix right before the query chain to determine
        which model (and thus table) is being queried.
        """
        # Find Model:: pattern closest to the query
        for model in imported_models:
            if f"{model}::" in query_context:
                return model_table_map.get(model)
        return None

    def _check_where_indexes(
        self, method_body: str, method_name: str, start_line: int,
        imported_models: Set[str], model_table_map: Dict[str, str],
        all_indexes: Dict[str, Set[str]], issues: List[Dict[str, Any]]
    ) -> None:
        """Check WHERE clauses for missing indexes, with per-query table resolution."""
        # Split method into query chains and analyze each
        # Find each query chain starting with Model::
        for model in imported_models:
            pattern = re.escape(model) + r"::\w+"
            for chain_match in re.finditer(pattern, method_body):
                # Get the rest of the chain (until ; or newline)
                chain_start = chain_match.start()
                chain_end = method_body.find(";", chain_start)
                if chain_end == -1:
                    chain_end = min(chain_start + 500, len(method_body))
                chain = method_body[chain_start:chain_end]

                table = model_table_map.get(model)
                if not table:
                    continue

                # Find where clauses in this chain
                for wp in re.finditer(r"->where(?:Not)?\(\s*['\"](\w+)['\"]", chain):
                    col = wp.group(1)
                    if col in ("id", "created_at", "updated_at"):
                        continue
                    if col not in all_indexes.get(table, set()):
                        issues.append({
                            "method": method_name,
                            "line": start_line,
                            "severity": "warning",
                            "code": "missing-index",
                            "message": f"Column '{col}' in WHERE on table '{table}' ({model}) — no index found",
                            "snippet": f"{model}::...->where('{col}', ...)",
                            "fix": f"Add index: $table->index('{col}') in migration",
                        })

    def _check_orderby_indexes(
        self, method_body: str, method_name: str, start_line: int,
        imported_models: Set[str], model_table_map: Dict[str, str],
        all_indexes: Dict[str, Set[str]], issues: List[Dict[str, Any]]
    ) -> None:
        """Check ORDER BY for missing indexes, with per-query table resolution."""
        for model in imported_models:
            pattern = re.escape(model) + r"::\w+"
            for chain_match in re.finditer(pattern, method_body):
                chain_start = chain_match.start()
                chain_end = method_body.find(";", chain_start)
                if chain_end == -1:
                    chain_end = min(chain_start + 500, len(method_body))
                chain = method_body[chain_start:chain_end]

                table = model_table_map.get(model)
                if not table:
                    continue

                for op in re.finditer(r"->orderBy(?:Desc)?\(\s*['\"](\w+)['\"]", chain):
                    col = op.group(1)
                    if col in ("id", "created_at", "updated_at"):
                        continue
                    if col not in all_indexes.get(table, set()):
                        issues.append({
                            "method": method_name,
                            "line": start_line,
                            "severity": "info",
                            "code": "unindexed-orderby",
                            "message": f"Column '{col}' in ORDER BY on table '{table}' ({model}) — no index",
                            "snippet": f"{model}::...->orderBy('{col}')",
                            "fix": f"Consider: $table->index('{col}')",
                        })

    @staticmethod
    def _check_queries_in_loops(
        method_lines: List[str], method_name: str, start_line: int,
        issues: List[Dict[str, Any]]
    ) -> None:
        """Detect database queries inside loops with proper brace-depth tracking."""
        loop_depth = 0
        brace_depth_at_loop: List[int] = []  # stack of brace depths when loops started
        current_brace_depth = 0

        query_patterns = [
            "::where(", "::find(", "::findOrFail(", "::first(",
            "->where(", "->find(", "->first(",
            "DB::table(", "DB::select(", "DB::insert(", "DB::update(",
        ]

        for i, line in enumerate(method_lines):
            stripped = line.strip()

            # Track brace depth
            current_brace_depth += stripped.count("{") - stripped.count("}")

            # Detect loop start
            if re.match(r'(foreach|for|while)\s*\(', stripped):
                loop_depth += 1
                brace_depth_at_loop.append(current_brace_depth)

            # Detect loop end (when brace depth drops back to loop's start level)
            if loop_depth > 0 and brace_depth_at_loop:
                if current_brace_depth <= brace_depth_at_loop[-1] - 1:
                    loop_depth -= 1
                    brace_depth_at_loop.pop()

            # Check for queries inside loops
            if loop_depth > 0:
                if any(pat in stripped for pat in query_patterns):
                    issues.append({
                        "method": method_name,
                        "line": start_line + i,
                        "severity": "error",
                        "code": "query-in-loop",
                        "message": "Database query inside loop — use eager loading or batch query",
                        "snippet": stripped[:120],
                    })

    def _cache_key_matches(self, pattern: str, key: str) -> bool:
        """Check if a cache key matches a pattern (supports * and {var})."""
        regex = re.escape(pattern)
        regex = regex.replace(r"\*", ".*")
        regex = re.sub(r"\\\{[^}]*\\\}", r"[^.]+", regex)
        # Also handle unescaped {$var} from dynamic keys
        regex = re.sub(r"\{\\\$[^}]*\}", r"[^.]+", regex)
        try:
            return bool(re.fullmatch(regex, key))
        except re.error:
            return False
