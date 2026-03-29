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

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService
from .generic_intelligence_service import GenericIntelligenceService
from .laravel_intelligence_service import LaravelIntelligenceService
from .laravel_validation_service import LaravelValidationService
from ..adapters.language_syntax import get_syntax_for_file, get_language_syntax, get_all_languages, LanguageSyntax

logger = logging.getLogger(__name__)

# Project-scoped session tracking — keyed by project path to prevent cross-project state leaks
_SESSION_STORE: Dict[str, Dict[str, Any]] = {}
_SESSION_MAX_AGE: int = 3600  # 1 hour TTL
_SESSION_MAX_ITEMS: int = 1000  # Max ripple items before cleanup


def _get_session(project_path: str = "") -> Dict[str, Any]:
    """Get or create session state scoped to a project path."""
    key = project_path or "_default"
    if key not in _SESSION_STORE:
        _SESSION_STORE[key] = {
            "ripple_items": {},
            "resolved": set(),
            "index_dirty": set(),
            "pipeline_calls": {},
            "decisions": [],
            "feedback_overrides": {},
            "metrics": {
                "total_edits": 0,
                "files_edited": set(),
                "blocked_edits": 0,
                "total_warnings": 0,
                "warnings_by_type": {},
                "total_ripple_items": 0,
                "resolved_ripple_items": 0,
                "avg_affected_per_edit": 0.0,
            },
            "start_time": time.time(),
        }
    return _SESSION_STORE[key]


# Backward-compatible aliases for existing code
_SESSION_RIPPLE_ITEMS: Dict[str, Dict[str, Any]] = {}
_SESSION_RESOLVED: Set[str] = set()
_SESSION_INDEX_DIRTY: Set[str] = set()
_SESSION_PIPELINE_CALLS: Dict[str, Set[str]] = {}
_SESSION_DECISIONS: List[Dict[str, Any]] = []
_SESSION_FEEDBACK_OVERRIDES: Dict[str, Dict] = {}
_SESSION_METRICS: Dict[str, Any] = {
    "total_edits": 0,
    "files_edited": set(),
    "blocked_edits": 0,
    "total_warnings": 0,
    "warnings_by_type": {},
    "total_ripple_items": 0,
    "resolved_ripple_items": 0,
    "avg_affected_per_edit": 0.0,
}
_SESSION_START_TIME: float = time.time()


def _check_session_cleanup(project_path: str = ""):
    """Clean up session state if too old or too large."""
    global _SESSION_START_TIME
    session = _get_session(project_path)
    now = time.time()
    if now - session.get("start_time", 0) > _SESSION_MAX_AGE or len(session["ripple_items"]) > _SESSION_MAX_ITEMS:
        # Clear project-scoped session
        session["ripple_items"].clear()
        session["resolved"].clear()
        session["index_dirty"].clear()
        session["pipeline_calls"].clear()
        session["decisions"].clear()
        session["feedback_overrides"].clear()
        session["metrics"] = {
            "total_edits": 0, "files_edited": set(), "blocked_edits": 0,
            "total_warnings": 0, "warnings_by_type": {},
            "total_ripple_items": 0, "resolved_ripple_items": 0,
            "avg_affected_per_edit": 0.0,
        }
        session["start_time"] = now
        # Also clear backward-compatible globals
        _SESSION_RIPPLE_ITEMS.clear()
        _SESSION_RESOLVED.clear()
        _SESSION_INDEX_DIRTY.clear()
        _SESSION_PIPELINE_CALLS.clear()
        _SESSION_DECISIONS.clear()
        _SESSION_FEEDBACK_OVERRIDES.clear()
        _SESSION_START_TIME = now

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

    # ── Compact Response System ──────────────────────────────────────

    @staticmethod
    def _save_to_session_file(tool_name: str, result: Dict[str, Any], project_path: str = "") -> str:
        """Save full result to session file, return the path.

        Appends to .blindspot/output/session_{pid}.json keeping the last 30 entries.
        This allows AI agents to read full details only when needed,
        keeping context window usage minimal.
        """
        detail_dir = os.path.join(project_path, ".blindspot", "output") if project_path else "/tmp/blindspot-output"
        os.makedirs(detail_dir, exist_ok=True)
        detail_path = os.path.join(detail_dir, f"session_{os.getpid()}.json")

        save_copy = {k: v for k, v in result.items() if k not in ("edited_content", "_project_path")}
        save_copy.pop("diff", None)
        save_copy["_tool"] = tool_name
        save_copy["_timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

        entries = []
        if os.path.isfile(detail_path):
            try:
                with open(detail_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                if not isinstance(entries, list):
                    entries = []
            except Exception:
                entries = []

        entries.append(save_copy)
        entries = entries[-30:]

        with open(detail_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, default=str)

        return detail_path

    @staticmethod
    def _generate_ripple_id(file_path: str, line: int, symbol: str) -> str:
        """Generate deterministic ripple item ID for cross-call tracking."""
        import hashlib
        raw = f"{file_path}:{line}:{symbol}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]

    @staticmethod
    def _get_truncation_limits(total_files: int) -> Dict[str, int]:
        """Adaptive truncation based on impact size."""
        if total_files <= 3:
            return {"max_files": 3, "max_lines": 20}
        elif total_files <= 10:
            return {"max_files": 5, "max_lines": 15}
        elif total_files <= 20:
            return {"max_files": 7, "max_lines": 10}
        else:
            return {"max_files": 5, "max_lines": 5}

    @staticmethod
    def _compact_smart_response(result: Dict[str, Any]) -> Dict[str, Any]:
        """Save full response to file, return minimal summary to context.

        AI agent can read the full details file if needed — saves context window.
        HIGH priority items include code snippets, MEDIUM/LOW are summarized.
        """
        base_path = result.get("_project_path", "")
        try:
            detail_path = AdvancedAnalysisService._save_to_session_file(
                "smart_apply_edit", result, base_path
            )
        except Exception:
            detail_path = None

        status = result.get("status", "success")
        summary: Dict[str, Any] = {
            "status": status,
            "file": result.get("file", result.get("file_path", "")),
            "detail_file": detail_path,
        }

        # Critical warnings only
        critical_warnings = [
            w for w in result.get("warnings", [])
            if w.get("severity") in ("CRITICAL", "HIGH")
        ]
        if critical_warnings:
            summary["warnings"] = [
                {"type": w.get("type", ""), "severity": w["severity"], "message": str(w.get("message", ""))[:120]}
                for w in critical_warnings
            ]

        # Ripple — HIGH: full details, MEDIUM/LOW: compact
        ripple_summary = []
        for rw in result.get("ripple_warnings", []):
            for af in rw.get("affected_files_with_code", rw.get("affected_files", [])):
                if isinstance(af, dict):
                    if af.get("priority") == "HIGH" and len(ripple_summary) < 3:
                        ripple_summary.append({
                            "file": af.get("file", ""),
                            "priority": "HIGH",
                            "lines": [
                                {"line": ln.get("line"), "code": str(ln.get("code", ""))[:120],
                                 "action": ln.get("action", "")}
                                for ln in af.get("lines", [])[:5]
                            ] if isinstance(af.get("lines"), list) else [],
                        })
                    else:
                        ripple_summary.append({
                            "file": af.get("file", ""),
                            "priority": af.get("priority", "LOW"),
                            "action_count": len(af.get("lines", [])) if isinstance(af.get("lines"), list) else af.get("lines", 0),
                        })
        if ripple_summary:
            summary["affected_files"] = ripple_summary

        # Scope direction
        if "scope_direction" in result:
            summary["scope_direction"] = result["scope_direction"]

        # Coverage
        if "ripple_coverage" in result:
            summary["coverage_percent"] = result["ripple_coverage"].get("coverage_percent", 0)

        # Edit summary — one line
        if "edit_summary" in result:
            es = result["edit_summary"]
            summary["edit_summary"] = (
                f"{es.get('total_affected_files', 0)} affected, "
                f"risk: {es.get('remaining_risk', 'low')}"
            )

        # Test suggestions — just first one
        tests = result.get("test_suggestions", [])
        if tests:
            summary["suggested_tests_count"] = len(tests)
            summary["hint"] = f"Run: {tests[0]}" if tests else None

        # Session metrics
        if "session_metrics" in result:
            sm = result["session_metrics"]
            summary["session"] = f"{sm.get('total_edits', 0)} edits, {sm.get('total_warnings', 0)} warnings"

        return summary

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
        Analyze ORM queries in a controller for performance issues.

        Detects:
        - N+1 query risks (relationship access without eager loading)
        - Missing database indexes on filtered/sorted columns
        - Unbounded queries without pagination on list endpoints
        - Queries inside loops
        - Missing column selection (fetching all columns)

        Currently optimized for PHP/Laravel Eloquent queries.
        For other frameworks, use framework-specific query analysis tools.

        Args:
            controller: Controller name (e.g., "UserController" or "Admin/OrderController")
            method: Optional method name. If omitted, analyzes ALL public methods.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Check if controller file exists — graceful degradation for non-PHP projects
        controller_file = self._find_controller_file(base, controller) if hasattr(self, '_find_controller_file') else None
        if not controller_file:
            # Try generic search
            from ..adapters.project_structure import get_project_structure
            structure = get_project_structure(base)
            found = False
            for rel_path, abs_path in structure.walk_source_files():
                if controller.lower() in os.path.basename(rel_path).lower():
                    found = True
                    break
            if not found:
                return {
                    "status": "not_applicable",
                    "message": f"Controller '{controller}' not found. This tool is optimized for PHP/Laravel. For other frameworks, use framework-specific tools.",
                }

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

        # Detect TypeORM/NestJS files and use specialized analysis
        is_ts = file_path.endswith((".ts", ".tsx", ".js", ".jsx"))
        if is_ts:
            return self._check_typeorm_eager_loading(file_path, content)

        # Find which models are involved (Laravel)
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

    def _check_typeorm_eager_loading(self, file_path: str, content: str) -> Dict[str, Any]:
        """
        Check TypeORM/NestJS files for N+1 query patterns.

        Detects:
        - Repository .find() calls without relations option
        - Loops that access entity relationships without preloading
        - QueryBuilder without leftJoinAndSelect for accessed relations
        """
        issues: List[Dict[str, Any]] = []
        lines = content.split("\n")

        # Find imported entities
        imported_entities: Set[str] = set()
        for line in lines:
            m = re.search(r'import\s*\{([^}]+)\}\s*from\s*[\'"].*entit', line)
            if m:
                for name in m.group(1).split(","):
                    name = name.strip()
                    if name and not name.startswith("type "):
                        imported_entities.add(name)

        # Find @InjectRepository usages to map variable -> entity
        repo_map: Dict[str, str] = {}  # variable name -> entity name
        for i, line in enumerate(lines):
            m = re.search(r'@InjectRepository\s*\(\s*(\w+)\s*\)', line)
            if m:
                entity = m.group(1)
                # Next line usually has: private readonly fooRepository: Repository<Foo>
                for j in range(i, min(i + 3, len(lines))):
                    vm = re.search(r'(?:private|protected|public)\s+(?:readonly\s+)?(\w+)\s*:', lines[j])
                    if vm:
                        repo_map[vm.group(1)] = entity
                        break

        # Find .find() / .findOne() calls without relations
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Pattern: repository.find({ where: ... }) without relations
            for repo_var in repo_map:
                if f'{repo_var}.find(' in stripped or f'{repo_var}.findOne(' in stripped:
                    # Check if relations are specified in the next few lines
                    context_block = "\n".join(lines[max(0, i - 1):min(len(lines), i + 10)])
                    has_relations = 'relations:' in context_block or 'relations :' in context_block
                    if not has_relations:
                        entity = repo_map[repo_var]
                        issues.append({
                            "line": i,
                            "severity": "info",
                            "code": "typeorm-no-relations",
                            "entity": entity,
                            "message": f"{repo_var}.find() without 'relations' option — related entities won't be loaded",
                            "snippet": stripped[:120],
                            "fix": f"Add relations: ['relationName'] to the find options if you need related data",
                        })

            # Pattern: accessing .property on entity inside a loop (for/map/forEach)
            # This is a heuristic — look for entity property access after a find
            if re.search(r'\.(map|forEach|filter|reduce|some|every)\s*\(', stripped):
                # Check callback body for relationship-like property access
                block = "\n".join(lines[max(0, i - 1):min(len(lines), i + 15)])
                # Look for patterns like item.relationship.property
                deep_access = re.findall(r'(\w+)\.(\w+)\.(\w+)', block)
                for _, rel, _ in deep_access:
                    if rel in ('length', 'map', 'filter', 'forEach', 'find', 'some',
                               'every', 'reduce', 'includes', 'indexOf', 'push',
                               'prototype', 'constructor', 'toString', 'data',
                               'status', 'message', 'error', 'type', 'id', 'name'):
                        continue
                    # Could be a relationship access — flag as potential
                    issues.append({
                        "line": i,
                        "severity": "info",
                        "code": "typeorm-deep-access-in-loop",
                        "message": f"Deep property access '.{rel}.' inside array iteration — verify relation is eager-loaded",
                        "snippet": stripped[:120],
                    })
                    break  # One warning per loop

        # Find QueryBuilder without joins for known relations
        qb_regions: List[Tuple[int, int]] = []
        for i, line in enumerate(lines, 1):
            if '.createQueryBuilder(' in line:
                qb_regions.append((i, min(i + 30, len(lines))))

        for start, end in qb_regions:
            block = "\n".join(lines[start - 1:end])
            has_join = 'leftJoinAndSelect' in block or 'innerJoinAndSelect' in block or 'leftJoin' in block
            has_get_many = '.getMany()' in block or '.getOne()' in block
            if has_get_many and not has_join:
                issues.append({
                    "line": start,
                    "severity": "info",
                    "code": "typeorm-qb-no-join",
                    "message": "QueryBuilder without join — related entities won't be loaded",
                    "snippet": lines[start - 1].strip()[:120],
                    "fix": "Add .leftJoinAndSelect() if you need related entities",
                })

        return {
            "status": "success",
            "file": file_path,
            "file_type": "typeorm-service",
            "eager_loaded": [],
            "models_analyzed": sorted(imported_entities),
            "issues": issues,
            "blade_issues": [],
            "summary": {
                "n_plus_one_risks": len([i for i in issues if i["severity"] == "warning"]),
                "info_items": len([i for i in issues if i["severity"] == "info"]),
                "eager_loaded_count": 0,
                "models_checked": len(imported_entities),
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

        Scans source files for cache operations and finds conflicts.
        Currently optimized for PHP/Laravel projects with Cache:: facade.
        For other frameworks, use framework-specific cache tools.

        Args:
            cache_key: Optional cache key pattern to filter results.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Check if this is a PHP/Laravel project
        if not os.path.isdir(os.path.join(base, "app", "Models")):
            return {
                "status": "not_applicable",
                "message": "detect_cache_conflicts is optimized for PHP/Laravel projects. Use framework-specific cache tools for other frameworks.",
            }

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
            if not isinstance(edit, dict):
                errors.append({"error": f"Each edit must be a dict with file_path/search/replace, got {type(edit).__name__}"})
                continue
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

    # ── full_audit ───────────────────────────────────────────────────

    def full_audit(self, focus: str = "all") -> Dict[str, Any]:
        """
        Run a comprehensive, language-agnostic project audit.

        Scans for security, performance, quality, and dead code issues
        across all source files using the LanguageSyntax adapter for
        language-specific pattern matching.

        Args:
            focus: Category to scan — "all", "security", "performance",
                   "quality", or "dead_code".

        Returns:
            Dict with issues grouped by category, each with severity,
            file, line, message, and fix suggestion.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        valid_categories = {"all", "security", "performance", "quality", "dead_code"}
        if focus not in valid_categories:
            return {"status": "error", "message": f"Invalid focus '{focus}'. Use: {sorted(valid_categories)}"}

        categories_to_run = (
            ["security", "performance", "quality", "dead_code"]
            if focus == "all"
            else [focus]
        )

        # Collect all source files
        from ..config import EXTENSION_LANGUAGE_MAP
        source_files: List[Tuple[str, str, str]] = []  # (rel_path, full_path, language)
        for root, _dirs, files in os.walk(base):
            # Skip common non-source directories
            rel_root = os.path.relpath(root, base)
            if any(skip in rel_root.split(os.sep) for skip in [
                "node_modules", "vendor", ".git", "__pycache__", ".mypy_cache",
                "dist", "build", ".next", "target", "coverage", "test_env",
                ".blindspot", ".venv", "venv", "env", ".tox", ".nox",
                "site-packages", ".eggs", "htmlcov", ".pytest_cache",
            ]):
                continue
            for fname in files:
                ext = os.path.splitext(fname)[1]
                lang = EXTENSION_LANGUAGE_MAP.get(ext)
                if lang:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, base)
                    source_files.append((rel, full, lang))

        results: Dict[str, List[Dict[str, Any]]] = {
            "security": [],
            "performance": [],
            "quality": [],
            "dead_code": [],
        }

        # Cap files to avoid extremely long scans
        source_files = source_files[:500]

        for rel_path, full_path, language in source_files:
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            syntax = get_language_syntax(language)
            lines = content.split("\n")

            if "security" in categories_to_run:
                self._audit_security(rel_path, lines, syntax, language, results["security"])
            if "performance" in categories_to_run:
                self._audit_performance(rel_path, lines, syntax, language, results["performance"])
            if "quality" in categories_to_run:
                self._audit_quality(rel_path, lines, syntax, language, results["quality"])

        # Dead code detection uses the deep index
        if "dead_code" in categories_to_run:
            self._audit_dead_code(base, source_files, results["dead_code"])

        # Build summary
        all_issues = []
        for cat in categories_to_run:
            all_issues.extend(results[cat])

        summary = {}
        for cat in categories_to_run:
            issues = results[cat]
            summary[cat] = {
                "total": len(issues),
                "critical": sum(1 for i in issues if i.get("severity") == "critical"),
                "error": sum(1 for i in issues if i.get("severity") == "error"),
                "warning": sum(1 for i in issues if i.get("severity") == "warning"),
                "info": sum(1 for i in issues if i.get("severity") == "info"),
            }

        full_result = {
            "status": "success",
            "focus": focus,
            "files_scanned": len(source_files),
            "issues": {cat: results[cat] for cat in categories_to_run},
            "summary": summary,
            "total_issues": len(all_issues),
        }

        # Always save full results to detail file, return compact summary
        try:
            base = self._get_project_path() or ""
            detail_path = self._save_to_session_file("full_audit", full_result, base)
            return {
                "status": "success",
                "focus": focus,
                "files_scanned": len(source_files),
                "summary": summary,
                "total_issues": len(all_issues),
                "detail_file": detail_path,
                "hint": "Full issue list saved to detail_file. Read it for specifics.",
            }
        except Exception:
            return full_result

    def _audit_security(
        self, file_path: str, lines: List[str], syntax: LanguageSyntax,
        language: str, issues: List[Dict[str, Any]]
    ) -> None:
        """Scan for security issues in a file."""
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if syntax.is_comment(stripped):
                continue

            # Hardcoded secrets: API keys, passwords, tokens
            secret_patterns = [
                (r'(?:api[_-]?key|apikey|secret[_-]?key|password|passwd|token|auth[_-]?token)\s*[=:]\s*["\'][^"\']{8,}["\']',
                 "Hardcoded secret detected"),
                (r'(?:AWS_SECRET|STRIPE_SECRET|DATABASE_PASSWORD|PRIVATE_KEY)\s*[=:]\s*["\'][^"\']+["\']',
                 "Hardcoded credential constant"),
            ]
            for pattern, msg in secret_patterns:
                if re.search(pattern, stripped, re.IGNORECASE):
                    # Skip env() calls or os.environ or process.env
                    if any(safe in stripped for safe in ["env(", "os.environ", "process.env", "getenv", "os.Getenv"]):
                        continue
                    issues.append({
                        "severity": "critical",
                        "file": file_path,
                        "line": i,
                        "code": "hardcoded-secret",
                        "message": msg,
                        "fix": "Use environment variables instead of hardcoded values",
                    })

            # Raw SQL without bindings
            raw_sql_patterns = {
                "php": [r'DB::raw\s*\(\s*["\'].*\$', r'->whereRaw\s*\(\s*["\'].*\$'],
                "python": [r'\.execute\s*\(\s*f["\']', r'\.execute\s*\(\s*["\'].*%\s*\(', r'cursor\.execute\s*\(\s*["\'].*\+'],
                "javascript": [r'\.query\s*\(\s*`[^`]*\$\{', r'\.query\s*\(\s*["\'].*\+'],
                "typescript": [r'\.query\s*\(\s*`[^`]*\$\{', r'\.query\s*\(\s*["\'].*\+'],
                "go": [r'\.Exec\s*\(\s*fmt\.Sprintf', r'\.Query\s*\(\s*fmt\.Sprintf'],
                "ruby": [r'\.execute\s*\(\s*".*#\{', r'\.where\s*\(\s*".*#\{'],
            }
            for pattern in raw_sql_patterns.get(language, []):
                if re.search(pattern, stripped):
                    issues.append({
                        "severity": "error",
                        "file": file_path,
                        "line": i,
                        "code": "raw-sql-injection",
                        "message": "Raw SQL with variable interpolation — SQL injection risk",
                        "fix": "Use parameterized queries or query builder bindings",
                    })

            # Mass assignment risks (language-specific)
            if language == "php":
                if re.search(r'->(?:create|update|fill)\s*\(\s*\$request->all\(\)', stripped):
                    issues.append({
                        "severity": "error",
                        "file": file_path,
                        "line": i,
                        "code": "mass-assignment",
                        "message": "Mass assignment with $request->all() — use validated() or only()",
                        "fix": "Use $request->validated() or $request->only([...]) instead",
                    })
            elif language == "python":
                if re.search(r'\.objects\.create\s*\(\s*\*\*request\.(data|POST)', stripped):
                    issues.append({
                        "severity": "error",
                        "file": file_path,
                        "line": i,
                        "code": "mass-assignment",
                        "message": "Mass assignment with **request.data — use serializer validation",
                        "fix": "Use a serializer with explicit fields instead of passing raw request data",
                    })
            elif language in ("javascript", "typescript"):
                if re.search(r'\.create\s*\(\s*req\.body\s*\)', stripped):
                    issues.append({
                        "severity": "error",
                        "file": file_path,
                        "line": i,
                        "code": "mass-assignment",
                        "message": "Mass assignment with req.body — validate and pick fields",
                        "fix": "Use a DTO or pick specific fields from req.body",
                    })

            # Unescaped output patterns
            if language == "php":
                if re.search(r'\{!!\s*\$', stripped) or re.search(r'echo\s+\$(?!this)', stripped):
                    issues.append({
                        "severity": "warning",
                        "file": file_path,
                        "line": i,
                        "code": "unescaped-output",
                        "message": "Unescaped output — potential XSS risk",
                        "fix": "Use {{ $var }} (escaped) instead of {!! $var !!} or echo",
                    })
            elif language in ("javascript", "typescript"):
                if re.search(r'dangerouslySetInnerHTML', stripped) or re.search(r'\.innerHTML\s*=', stripped):
                    issues.append({
                        "severity": "warning",
                        "file": file_path,
                        "line": i,
                        "code": "unescaped-output",
                        "message": "Direct HTML injection — potential XSS risk",
                        "fix": "Sanitize HTML input or use safe rendering methods",
                    })

    def _audit_performance(
        self, file_path: str, lines: List[str], syntax: LanguageSyntax,
        language: str, issues: List[Dict[str, Any]]
    ) -> None:
        """Scan for performance issues in a file."""
        in_loop = False
        loop_depth = 0
        brace_depth = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if syntax.is_comment(stripped):
                continue

            # Track loop context (brace-based languages)
            if language in ("php", "javascript", "typescript", "go", "rust", "java"):
                brace_depth += stripped.count("{") - stripped.count("}")
                if re.match(r'(for|foreach|while)\s*\(', stripped):
                    loop_depth += 1
                if loop_depth > 0 and brace_depth <= 0:
                    loop_depth = max(0, loop_depth - 1)
            elif language == "python":
                if re.match(r'(for|while)\s+', stripped):
                    in_loop = True
                elif in_loop and stripped and not stripped.startswith((' ', '\t')) and not stripped.startswith('#'):
                    in_loop = False

            is_in_loop = loop_depth > 0 or in_loop

            # Unbounded queries (queries without limits in loops)
            query_in_loop_patterns = {
                "php": [r'::where\(', r'::find\(', r'DB::table\('],
                "python": [r'\.objects\.(filter|get|all)\(', r'\.execute\(', r'session\.query\('],
                "javascript": [r'\.find\(', r'\.findOne\(', r'await\s+.*\.query\('],
                "typescript": [r'\.find\(', r'\.findOne\(', r'\.getRepository\('],
                "go": [r'\.Find\(', r'\.First\(', r'\.Where\('],
                "ruby": [r'\.where\(', r'\.find\(', r'\.find_by\('],
            }
            if is_in_loop:
                for pattern in query_in_loop_patterns.get(language, []):
                    if re.search(pattern, stripped):
                        issues.append({
                            "severity": "error",
                            "file": file_path,
                            "line": i,
                            "code": "query-in-loop",
                            "message": "Database query inside loop — batch or eager-load instead",
                            "fix": "Move query outside loop, use batch query, or eager loading",
                        })
                        break

            # Missing pagination on list-like queries
            pagination_check = {
                "php": (r'->get\(\s*\)', r'paginate\('),
                "python": (r'\.all\(\s*\)', r'\.paginate\(|Paginator|PageNumberPagination|\[:.*\]'),
                "javascript": (r'\.find\(\s*\{\s*\}\s*\)', r'\.limit\(|\.skip\(|\.paginate\('),
                "typescript": (r'\.find\(\s*\{\s*\}\s*\)', r'\.take\(|\.skip\(|\.limit\('),
            }
            if language in pagination_check:
                fetch_pat, page_pat = pagination_check[language]
                if re.search(fetch_pat, stripped):
                    # Check surrounding lines for pagination
                    context_block = "\n".join(lines[max(0, i - 5):min(len(lines), i + 5)])
                    if not re.search(page_pat, context_block):
                        issues.append({
                            "severity": "warning",
                            "file": file_path,
                            "line": i,
                            "code": "unbounded-query",
                            "message": "Query fetches all records without pagination or limit",
                            "fix": "Add pagination or limit to prevent loading unbounded data",
                        })

            # Large file reads without streaming
            file_read_patterns = {
                "php": r'file_get_contents\(',
                "python": r'\.read\(\s*\)|\.readlines\(\s*\)',
                "javascript": r'readFileSync\(|readFile\(',
                "go": r'ioutil\.ReadAll\(|os\.ReadFile\(',
                "rust": r'fs::read_to_string\(',
            }
            pat = file_read_patterns.get(language)
            if pat and re.search(pat, stripped):
                issues.append({
                    "severity": "info",
                    "file": file_path,
                    "line": i,
                    "code": "large-file-read",
                    "message": "File read loads entire content into memory — consider streaming for large files",
                    "fix": "Use streaming/buffered reads for potentially large files",
                })

    def _audit_quality(
        self, file_path: str, lines: List[str], syntax: LanguageSyntax,
        language: str, issues: List[Dict[str, Any]]
    ) -> None:
        """Scan for code quality issues in a file."""
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Debug statements (using language-specific debug functions from syntax adapter)
            for debug_fn in syntax.debug_functions:
                if debug_fn in stripped and not syntax.is_comment(stripped):
                    issues.append({
                        "severity": "warning",
                        "file": file_path,
                        "line": i,
                        "code": "debug-statement",
                        "message": f"Debug statement '{debug_fn}' left in code",
                        "fix": f"Remove {debug_fn} before committing",
                    })
                    break  # One per line

            # TODO/FIXME/HACK comments
            if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped):
                tag = re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped).group(1)
                severity = "warning" if tag in ("FIXME", "HACK") else "info"
                issues.append({
                    "severity": severity,
                    "file": file_path,
                    "line": i,
                    "code": f"{tag.lower()}-comment",
                    "message": f"{tag} comment found: {stripped[:100]}",
                    "fix": f"Address the {tag} item or create a tracking issue",
                })

            # Empty catch blocks (language-specific)
            if language in ("php", "javascript", "typescript", "java"):
                if re.search(r'catch\s*\([^)]*\)\s*\{\s*\}', stripped):
                    issues.append({
                        "severity": "warning",
                        "file": file_path,
                        "line": i,
                        "code": "empty-catch",
                        "message": "Empty catch block — errors are silently swallowed",
                        "fix": "Log the error or handle it appropriately",
                    })
            elif language == "python":
                if re.match(r'except.*:\s*$', stripped):
                    # Check if next non-empty line is just 'pass'
                    for j in range(i, min(i + 3, len(lines))):
                        next_stripped = lines[j].strip()
                        if next_stripped and next_stripped != "":
                            if next_stripped == "pass":
                                issues.append({
                                    "severity": "warning",
                                    "file": file_path,
                                    "line": i,
                                    "code": "empty-catch",
                                    "message": "Bare except with pass — errors are silently swallowed",
                                    "fix": "Log the error or handle it; avoid bare except clauses",
                                })
                            break
            elif language == "go":
                if re.search(r'if\s+err\s*!=\s*nil\s*\{\s*\}', stripped):
                    issues.append({
                        "severity": "warning",
                        "file": file_path,
                        "line": i,
                        "code": "empty-catch",
                        "message": "Empty error handling block — errors are silently ignored",
                        "fix": "Return or log the error",
                    })

            # Unused imports (basic heuristic: import on one line, symbol not used elsewhere)
            # Only for single-symbol imports to reduce false positives
            if language == "python" and re.match(r'(?:from\s+\S+\s+)?import\s+(\w+)\s*$', stripped):
                imported_name = re.match(r'(?:from\s+\S+\s+)?import\s+(\w+)\s*$', stripped).group(1)
                full_content = "\n".join(lines)
                # Count occurrences of the import name (excluding the import line itself)
                usage_count = full_content.count(imported_name) - 1
                if usage_count <= 0:
                    issues.append({
                        "severity": "info",
                        "file": file_path,
                        "line": i,
                        "code": "unused-import",
                        "message": f"Import '{imported_name}' appears unused",
                        "fix": f"Remove unused import: {imported_name}",
                    })

    def _audit_dead_code(
        self, base: str, source_files: List[Tuple[str, str, str]],
        issues: List[Dict[str, Any]]
    ) -> None:
        """Detect functions/methods that are never referenced elsewhere.

        Uses the deep index for symbol lookup and find_references for
        cross-referencing.
        """
        try:
            from ..indexing import get_index_manager
            index_mgr = get_index_manager()
            if not index_mgr or not index_mgr.get_index_stats().get("status") == "loaded":
                issues.append({
                    "severity": "info",
                    "file": "",
                    "line": 0,
                    "code": "dead-code-skip",
                    "message": "Deep index not built — skipping dead code detection. Run build_deep_index first.",
                    "fix": "Run build_deep_index to enable dead code detection",
                })
                return
        except Exception:
            return

        intel = self._get_generic_intel()

        # Sample up to 100 files for dead code analysis to keep it fast
        sampled = source_files[:100]
        checked = 0

        for rel_path, full_path, language in sampled:
            summary = index_mgr.get_file_summary(rel_path)
            if not summary:
                continue

            # Check functions and methods
            for pool_key in ("functions", "methods"):
                for sym_info in summary.get(pool_key, []):
                    name = sym_info.get("name", "")
                    if not name:
                        continue
                    # Skip common entry points and lifecycle methods
                    base_name = name.split(".")[-1] if "." in name else name
                    if base_name.startswith("_") or base_name in (
                        "main", "__init__", "__str__", "__repr__", "setUp", "tearDown",
                        "test", "handle", "register", "boot", "run", "index", "create",
                        "store", "show", "edit", "update", "destroy",
                    ) or base_name.startswith("test_"):
                        continue

                    checked += 1
                    if checked > 200:
                        return  # Cap for performance

                    try:
                        refs = intel.find_references(base_name, scope="all")
                        ref_list = refs.get("references", [])
                        # Filter out self-references (same file)
                        external_refs = [
                            r for r in ref_list
                            if r.get("file", "") != rel_path
                        ]
                        if len(external_refs) == 0:
                            issues.append({
                                "severity": "info",
                                "file": rel_path,
                                "line": sym_info.get("line", 0),
                                "code": "dead-code",
                                "message": f"'{base_name}' has no external references — potentially dead code",
                                "fix": f"Verify '{base_name}' is needed; remove if unused",
                            })
                    except Exception:
                        continue

    # ── post_edit_checklist ──────────────────────────────────────────

    def post_edit_checklist(self, file_path: str) -> Dict[str, Any]:
        """
        Generate a language-aware checklist of steps to take after editing a file.

        Based on file extension and project type, returns required and
        recommended next steps (syntax checks, tests, cache clears, etc.).

        Args:
            file_path: Relative path to the edited file.

        Returns:
            Dict with checklist items, each with command, priority, and reason.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        ext = os.path.splitext(file_path)[1].lower()
        basename = os.path.basename(file_path).lower()
        checklist: List[Dict[str, Any]] = []

        # ── Language-specific syntax/type checks ──
        if ext == ".php":
            full_path = os.path.join(base, file_path)
            checklist.append({
                "command": f"php -l {file_path}",
                "priority": "required",
                "reason": "PHP syntax check to catch parse errors",
            })
            if "test" in file_path.lower():
                checklist.append({
                    "command": f"php artisan test --filter={os.path.splitext(basename)[0]}",
                    "priority": "required",
                    "reason": "Run the edited test file",
                })
        elif ext in (".ts", ".tsx"):
            checklist.append({
                "command": "npx tsc --noEmit",
                "priority": "required",
                "reason": "TypeScript type check to catch type errors",
            })
            if "test" in file_path.lower() or "spec" in file_path.lower():
                checklist.append({
                    "command": f"npx jest --testPathPattern={file_path}",
                    "priority": "required",
                    "reason": "Run the edited test/spec file",
                })
                checklist.append({
                    "command": f"npx vitest run {file_path}",
                    "priority": "optional",
                    "reason": "Alternative: run with Vitest if project uses it",
                })
        elif ext in (".js", ".jsx", ".mjs", ".cjs"):
            if "test" in file_path.lower() or "spec" in file_path.lower():
                checklist.append({
                    "command": f"npx jest --testPathPattern={file_path}",
                    "priority": "required",
                    "reason": "Run the edited test/spec file",
                })
            checklist.append({
                "command": f"node --check {file_path}",
                "priority": "recommended",
                "reason": "JavaScript syntax check",
            })
        elif ext == ".py":
            checklist.append({
                "command": f"python -m py_compile {file_path}",
                "priority": "required",
                "reason": "Python syntax/compile check",
            })
            if "test" in file_path.lower():
                checklist.append({
                    "command": f"pytest {file_path} -v",
                    "priority": "required",
                    "reason": "Run the edited test file",
                })
        elif ext == ".go":
            checklist.append({
                "command": "go vet ./...",
                "priority": "required",
                "reason": "Go static analysis check",
            })
            checklist.append({
                "command": "go test ./...",
                "priority": "recommended",
                "reason": "Run Go tests to verify changes",
            })
        elif ext == ".rs":
            checklist.append({
                "command": "cargo check",
                "priority": "required",
                "reason": "Rust compilation check",
            })
            checklist.append({
                "command": "cargo test",
                "priority": "recommended",
                "reason": "Run Rust tests to verify changes",
            })
        elif ext == ".rb":
            checklist.append({
                "command": f"ruby -c {file_path}",
                "priority": "required",
                "reason": "Ruby syntax check",
            })
            if "test" in file_path.lower() or "spec" in file_path.lower():
                checklist.append({
                    "command": f"bundle exec rspec {file_path}",
                    "priority": "required",
                    "reason": "Run the edited spec/test file",
                })
        elif ext == ".java":
            checklist.append({
                "command": "mvn compile -q",
                "priority": "required",
                "reason": "Java compilation check",
            })
            if "test" in file_path.lower():
                checklist.append({
                    "command": f"mvn test -pl . -Dtest={os.path.splitext(basename)[0]}",
                    "priority": "required",
                    "reason": "Run the edited test class",
                })

        # ── Config files ──
        if ext in (".yaml", ".yml", ".json", ".toml", ".ini", ".env"):
            checklist.append({
                "command": "Restart application server / reload config",
                "priority": "required",
                "reason": "Configuration changes require service restart to take effect",
            })
            if ext in (".env",) or basename.startswith(".env"):
                checklist.append({
                    "command": "Verify sensitive values are not committed to version control",
                    "priority": "required",
                    "reason": "Environment files may contain secrets",
                })

        # ── Migration files ──
        if "migration" in file_path.lower():
            if ext == ".php":
                checklist.append({
                    "command": "php artisan migrate",
                    "priority": "required",
                    "reason": "Run migration to apply database changes",
                })
            elif ext == ".py":
                checklist.append({
                    "command": "python manage.py migrate",
                    "priority": "required",
                    "reason": "Run Django migration to apply database changes",
                })
            elif ext == ".rb":
                checklist.append({
                    "command": "bundle exec rails db:migrate",
                    "priority": "required",
                    "reason": "Run Rails migration to apply database changes",
                })
            elif ext == ".go":
                checklist.append({
                    "command": "Run your migration tool (e.g., goose up, migrate up)",
                    "priority": "required",
                    "reason": "Apply database migration",
                })

        # ── Route files ──
        is_route_file = any(k in file_path.lower() for k in ["route", "urls.py", "router"])
        if is_route_file:
            if ext == ".php":
                checklist.append({
                    "command": "php artisan route:clear && php artisan route:cache",
                    "priority": "required",
                    "reason": "Clear and rebuild route cache after route changes",
                })
            checklist.append({
                "command": "Verify all route handlers/controllers exist",
                "priority": "recommended",
                "reason": "New routes need corresponding handlers",
            })

        # ── Docker files ──
        if basename in ("dockerfile", "docker-compose.yml", "docker-compose.yaml") or basename.startswith("dockerfile"):
            checklist.append({
                "command": "docker-compose build" if "compose" in basename else "docker build .",
                "priority": "required",
                "reason": "Rebuild container image to apply Docker changes",
            })
            checklist.append({
                "command": "docker-compose up -d" if "compose" in basename else "Restart container",
                "priority": "recommended",
                "reason": "Restart containers with new image",
            })

        # ── Test files (generic — if not already caught above) ──
        if not any(c.get("reason", "").startswith("Run the edited test") for c in checklist):
            if "test" in file_path.lower() or "spec" in file_path.lower():
                checklist.append({
                    "command": f"Run test: {file_path}",
                    "priority": "required",
                    "reason": "Test file was edited — verify it still passes",
                })

        return {
            "status": "success",
            "file": file_path,
            "extension": ext,
            "checklist": checklist,
            "total_items": len(checklist),
            "required_count": sum(1 for c in checklist if c["priority"] == "required"),
            "recommended_count": sum(1 for c in checklist if c["priority"] == "recommended"),
            "optional_count": sum(1 for c in checklist if c["priority"] == "optional"),
        }

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
        pipeline_context: dict = None,
        resolved_items: list = None,
        strict_mode: dict = None,
        feedback: dict = None,
    ) -> Dict[str, Any]:
        """
        The ultimate single-call edit tool. apply_edit on steroids.

        Does everything in ONE call:
        1. Applies the edit with syntax check + auto-rollback
        2. Runs anti-pattern check (project rule violations)
        3. Detects which symbols were changed
        4. Runs ripple effect on changed symbols with classification
        5. Shows affected files' RELEVANT CODE SNIPPETS so you can fix them too
        6. Shows cache keys that need invalidation review
        7. Tracks session-level ripple coverage
        8. Suggests relevant test commands
        9. Detects scope direction changes (narrowing/widening)

        The key innovation: when ripple effect finds affected files, it doesn't
        just list file names — it shows the EXACT lines in those files that
        reference the changed symbol, so you can update them without Read calls.

        Args: Same as apply_edit — all 5 modes supported.
        """
        base = self._get_project_path() or ""
        _check_session_cleanup(base)
        session = _get_session(base)

        from .file_edit_service import FileEditService

        edit_svc = FileEditService(self.ctx)
        intel = self._get_generic_intel()

        # Pipeline enforcement: check if get_context_for_edit was called first
        if strict_mode and strict_mode.get("enforce_pipeline"):
            pipeline_calls = session["pipeline_calls"].get(file_path, set())
            if "context" not in pipeline_calls:
                session["metrics"]["blocked_edits"] += 1
                return {
                    "status": "blocked",
                    "message": "Pipeline enforcement: call get_context_for_edit() before smart_apply_edit on high-risk files",
                    "missing_pipeline_steps": ["get_context_for_edit"],
                }

        # Sync global aliases with project-scoped session for backward compat
        # All reads/writes below go through these references which point to session data
        global _SESSION_RIPPLE_ITEMS, _SESSION_RESOLVED, _SESSION_INDEX_DIRTY
        global _SESSION_PIPELINE_CALLS, _SESSION_DECISIONS, _SESSION_FEEDBACK_OVERRIDES, _SESSION_METRICS
        _SESSION_RIPPLE_ITEMS = session["ripple_items"]
        _SESSION_RESOLVED = session["resolved"]
        _SESSION_INDEX_DIRTY = session["index_dirty"]
        _SESSION_PIPELINE_CALLS = session["pipeline_calls"]
        _SESSION_DECISIONS = session["decisions"]
        _SESSION_FEEDBACK_OVERRIDES = session["feedback_overrides"]
        _SESSION_METRICS = session["metrics"]

        # Process human feedback overrides
        if feedback:
            for ripple_id, fb in feedback.items():
                _SESSION_FEEDBACK_OVERRIDES[ripple_id] = fb

        # Track resolved items from previous calls
        if resolved_items:
            for item_id in resolved_items:
                _SESSION_RESOLVED.add(item_id)
                if item_id in _SESSION_RIPPLE_ITEMS:
                    _SESSION_RIPPLE_ITEMS[item_id]["state"] = "resolved"

        # Update session metrics
        _SESSION_METRICS["total_edits"] += 1
        _SESSION_METRICS["files_edited"].add(file_path)

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

        # Track this file as dirty for re-edit warnings
        if file_path in _SESSION_INDEX_DIRTY:
            result["re_edit_warning"] = f"File {file_path} was already edited this session. Verify previous changes first."
        _SESSION_INDEX_DIRTY.add(file_path)

        base = self._get_project_path()
        if not base:
            return result

        # Scope direction analysis when old/new code is available
        scope_direction = self._detect_scope_direction(search, replace, edits)
        if scope_direction:
            result["scope_direction"] = scope_direction

        # Generate test suggestions based on file type
        test_suggestions = self._generate_test_suggestions(file_path)
        if test_suggestions:
            result["test_suggestions"] = test_suggestions

        # Determine if file is high-risk for deep analysis
        # Language-agnostic: check if it's a model, service, controller, or core logic
        from ..config import EXTENSION_LANGUAGE_MAP
        ext = os.path.splitext(file_path)[1]
        lang = EXTENSION_LANGUAGE_MAP.get(ext)

        high_risk_patterns = [
            # PHP/Laravel
            "app/Models/", "app/Services/", "app/Http/Controllers/",
            "app/Http/Middleware/", "app/Http/Requests/",
            # Python/Django
            "models.py", "models/", "views.py", "views/", "services/", "middleware/",
            # JS/TS/NestJS/Next.js
            "src/models/", "src/services/", "src/controllers/", "src/entities/",
            "src/modules/", "lib/", "core/",
            # Go
            "internal/", "pkg/", "cmd/",
            # Rust
            "src/lib.rs", "src/models/", "src/services/",
            # Generic
            "domain/", "repository/", "handler/",
        ]
        is_high_risk = any(p in file_path for p in high_risk_patterns)

        if not is_high_risk:
            return result

        # Detect what changed
        changed_symbols = self._detect_changed_symbols(search, replace, edits, symbol)

        if not changed_symbols:
            return result

        ripple_warnings: List[Dict[str, Any]] = []
        all_ripple_items: List[Dict[str, Any]] = []
        items_no_action: List[Dict[str, Any]] = []

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

                # Collect affected files with code snippets and classify each
                affected_with_code: List[Dict[str, Any]] = []
                seen_aff: Set[str] = set()

                for impact in all_impacts[:10]:
                    imp_file = impact.get("file", "")
                    if not imp_file or imp_file == file_path or imp_file in seen_aff:
                        continue
                    seen_aff.add(imp_file)

                    # Classify this ripple item
                    classification = self._classify_ripple_item(
                        impact, search_term, sym, file_path, base
                    )

                    # Track in session
                    ripple_key = f"{sym}:{imp_file}:{impact.get('line', 0)}"
                    ripple_item = {
                        "file": imp_file,
                        "symbol": sym,
                        "line": impact.get("line", 0),
                        "classification": classification,
                    }
                    all_ripple_items.append(ripple_item)
                    _SESSION_RIPPLE_ITEMS[ripple_key] = ripple_item

                    if classification == "no_action":
                        items_no_action.append(ripple_item)
                        _SESSION_RESOLVED.add(ripple_key)

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
                                    "classification": classification,
                                })
                                existing["usages"] += 1
                        else:
                            affected_with_code.append({
                                "file": imp_file,
                                "usages": 1,
                                "classification": classification,
                                "lines": [{"line": imp_line, "code": imp_text.strip()[:150], "classification": classification}],
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
                                    "classification": classification,
                                })
                        if relevant_lines:
                            affected_with_code.append({
                                "file": imp_file,
                                "usages": len(relevant_lines),
                                "classification": classification,
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

                # Build priority for deterministic sorting
                priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3}
                priority_val = priority_map.get(risk, 3)

                warning_entry: Dict[str, Any] = {
                    "symbol": sym,
                    "risk_level": risk,
                    "_sort_priority": priority_val,
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

        # Derive symbol name and total affected for summaries
        symbol_name = changed_symbols[0] if changed_symbols else "unknown"
        total_affected = sum(w.get("total_files_affected", 0) for w in ripple_warnings)

        if ripple_warnings:
            # Deterministic sort: priority (HIGH->MEDIUM->LOW) -> file path -> line
            ripple_warnings.sort(key=lambda w: (
                w.get("_sort_priority", 3),
                (w.get("affected_files_with_code", [{}])[0].get("file", "") if w.get("affected_files_with_code") else ""),
                (w.get("affected_files_with_code", [{}])[0].get("lines", [{}])[0].get("line", 0) if w.get("affected_files_with_code") else 0),
            ))
            # Remove sort key from output
            for w in ripple_warnings:
                w.pop("_sort_priority", None)

            # Decision rationale and ripple IDs per warning
            for w in ripple_warnings:
                w["rationale"] = f"{w.get('risk_level', 'review')}: {w.get('symbol', symbol_name)} affects {w.get('total_files_affected', 0)} files"
                w["id"] = self._generate_ripple_id(
                    w.get("affected_files_with_code", [{}])[0].get("file", "") if w.get("affected_files_with_code") else "",
                    (w.get("affected_files_with_code", [{}])[0].get("lines", [{}])[0].get("line", 0) if w.get("affected_files_with_code") else 0),
                    symbol_name,
                )

            # Register ripple items with lifecycle
            for item in all_ripple_items:
                rid = self._generate_ripple_id(item["file"], item.get("line", 0), symbol_name)
                state = "open"
                if rid in _SESSION_RESOLVED:
                    state = "reopened"  # Was resolved but file re-edited
                _SESSION_RIPPLE_ITEMS[rid] = {
                    "file": item["file"],
                    "line": item.get("line", 0),
                    "symbol": symbol_name,
                    "action": item.get("classification", "review_logic"),
                    "state": state,
                    "id": rid,
                }

            result["ripple_warnings"] = ripple_warnings
            _SESSION_METRICS["total_warnings"] += len(ripple_warnings)

        # Count priority levels for edit summary
        high_count = sum(
            1 for w in ripple_warnings
            for af in w.get("affected_files_with_code", [])
            if isinstance(af, dict) and af.get("classification") == "fix_required"
        )
        med_count = sum(
            1 for w in ripple_warnings
            for af in w.get("affected_files_with_code", [])
            if isinstance(af, dict) and af.get("classification") in ("check_redundancy", "review_logic")
        )

        # Build edit summary
        result["edit_summary"] = {
            "changed_symbols": changed_symbols[:5],
            "total_affected_files": total_affected,
            "high_priority_items": high_count,
            "medium_priority_items": med_count,
            "remaining_risk": "critical" if high_count > 5 else "high" if high_count > 0 else "medium" if med_count > 0 else "low",
        }

        # Auto-fix suggestions for HIGH priority (fix_required) items
        fix_suggestions = []
        for w in ripple_warnings:
            for af in w.get("affected_files_with_code", []):
                if isinstance(af, dict) and af.get("classification") == "fix_required":
                    for ln in af.get("lines", [])[:2]:
                        fix_suggestions.append({
                            "file": af["file"],
                            "suggestion": f"Update usage of '{symbol_name}' to match new behavior",
                            "line": ln.get("line", 0),
                        })
        if fix_suggestions:
            result["fix_suggestions"] = fix_suggestions[:5]

        # Coverage tracking
        if all_ripple_items:
            total_items = len(all_ripple_items)
            resolved_count = len(items_no_action)
            coverage_pct = (resolved_count / total_items * 100) if total_items > 0 else 100.0
            result["ripple_coverage"] = {
                "total_items": total_items,
                "resolved_items": resolved_count,
                "coverage_percent": round(coverage_pct, 1),
                "needs_attention": total_items - resolved_count,
            }

        # Record decision in session history
        _SESSION_DECISIONS.append({
            "file": file_path,
            "symbol": symbol_name if changed_symbols else "unknown",
            "affected_count": total_affected,
            "timestamp": time.strftime("%H:%M:%S"),
        })

        # Update session metrics fully
        _SESSION_METRICS["total_warnings"] = _SESSION_METRICS.get("total_warnings", 0)
        _SESSION_METRICS["total_ripple_items"] = len(_SESSION_RIPPLE_ITEMS)
        _SESSION_METRICS["resolved_ripple_items"] = len(_SESSION_RESOLVED)
        total_edits = _SESSION_METRICS["total_edits"]
        if total_edits > 0:
            _SESSION_METRICS["avg_affected_per_edit"] = (
                _SESSION_METRICS.get("avg_affected_per_edit", 0) * (total_edits - 1) + total_affected
            ) / total_edits

        # Session-level metrics
        result["session_metrics"] = {
            "total_edits": _SESSION_METRICS["total_edits"],
            "files_edited": len(_SESSION_METRICS["files_edited"]),
            "total_ripple_items": len(_SESSION_RIPPLE_ITEMS),
            "total_resolved": len(_SESSION_RESOLVED),
            "total_warnings": _SESSION_METRICS["total_warnings"],
            "blocked_edits": _SESSION_METRICS["blocked_edits"],
            "avg_affected_per_edit": round(_SESSION_METRICS["avg_affected_per_edit"], 1),
        }

        # Save full response and return compact summary
        result["_project_path"] = base or ""
        return self._compact_smart_response(result)

    @staticmethod
    def _classify_ripple_item(
        impact: Dict[str, Any], search_term: str, symbol: str,
        edited_file: str, base: str,
    ) -> str:
        """Classify a ripple impact item into an action category.

        Returns one of:
        - "no_action": auto-inherits change, safe to skip
        - "check_redundancy": inline check may duplicate scope, review
        - "fix_required": direct usage will break, must fix
        - "review_logic": complex logic needs human review
        """
        imp_file = impact.get("file", "")
        imp_text = impact.get("text", "")
        usage_type = impact.get("type", "")

        if not imp_text:
            # No text available to classify — conservative default
            return "review_logic"

        stripped = imp_text.strip()

        # Import/use statements auto-inherit — no action needed
        if any(kw in stripped for kw in ["import ", "use ", "require(", "require_relative"]):
            return "no_action"

        # Type hints / annotations auto-inherit
        if any(kw in stripped for kw in [": " + search_term, "-> " + search_term,
                                          "@param", "@return", "@var"]):
            return "no_action"

        # Class extension inherits automatically
        if any(kw in stripped for kw in ["extends ", "implements ", "< "]):
            return "no_action"

        # Direct method call or property access — likely needs fixing
        if re.search(rf'(?:->|\.){re.escape(search_term)}\s*\(', stripped):
            return "fix_required"

        # Static call — likely needs fixing
        if re.search(rf'::{re.escape(search_term)}\s*\(', stripped):
            return "fix_required"

        # Inline condition that duplicates a scope — check redundancy
        if re.search(rf'(?:where|filter|if).*{re.escape(search_term)}', stripped, re.IGNORECASE):
            return "check_redundancy"

        # Complex logic (conditionals, ternary, switch) — needs human review
        if any(kw in stripped for kw in ["if ", "switch ", "case ", "? ", "match "]):
            return "review_logic"

        # Default: review needed
        return "review_logic"

    @staticmethod
    def _detect_scope_direction(
        search: str = None, replace: str = None, edits: list = None,
    ) -> Optional[str]:
        """Detect if the edit narrows or widens a scope/condition.

        Returns: "narrowing", "widening", "modified", or None if not detectable.
        """
        pairs = []
        if search and replace:
            pairs.append((search, replace))
        if edits:
            for e in edits:
                s = e.get("search", "")
                r = e.get("replace", "")
                if s and r:
                    pairs.append((s, r))

        if not pairs:
            return None

        narrowing_signals = 0
        widening_signals = 0

        for old_text, new_text in pairs:
            # Count restrictive operators / conditions
            restrictive = ["&&", "and ", "!==", "!=", "> ", "< ", ">=", "<=", "not ", "!"]
            permissive = ["||", "or ", "===", "==", "true", "all", "any"]

            old_restrict = sum(1 for r in restrictive if r in old_text)
            new_restrict = sum(1 for r in restrictive if r in new_text)
            old_permissive = sum(1 for p in permissive if p in old_text)
            new_permissive = sum(1 for p in permissive if p in new_text)

            if new_restrict > old_restrict or new_permissive < old_permissive:
                narrowing_signals += 1
            if new_permissive > old_permissive or new_restrict < old_restrict:
                widening_signals += 1

        if narrowing_signals > 0 and widening_signals == 0:
            return "narrowing"
        elif widening_signals > 0 and narrowing_signals == 0:
            return "widening"
        elif narrowing_signals > 0 and widening_signals > 0:
            return "modified"
        return None

    @staticmethod
    def _generate_test_suggestions(file_path: str) -> List[Dict[str, str]]:
        """Generate language-appropriate test commands based on file type."""
        ext = os.path.splitext(file_path)[1].lower()
        suggestions: List[Dict[str, str]] = []

        # Derive a test filter from the file name
        basename = os.path.splitext(os.path.basename(file_path))[0]
        dirname = os.path.dirname(file_path)

        if ext == ".php":
            suggestions.append({
                "command": f"php artisan test --filter={basename}",
                "reason": "Run related PHP tests",
            })
        elif ext in (".js", ".jsx", ".mjs"):
            suggestions.append({
                "command": f"npx jest --testPathPattern={basename}",
                "reason": "Run related Jest tests",
            })
            suggestions.append({
                "command": f"npx vitest run {file_path}",
                "reason": "Run with Vitest (if applicable)",
            })
        elif ext in (".ts", ".tsx"):
            suggestions.append({
                "command": f"npx jest --testPathPattern={basename}",
                "reason": "Run related Jest tests",
            })
            suggestions.append({
                "command": f"npx vitest run {file_path}",
                "reason": "Run with Vitest (if applicable)",
            })
        elif ext == ".py":
            suggestions.append({
                "command": f"pytest -k {basename} -v",
                "reason": "Run related Python tests",
            })
        elif ext == ".go":
            pkg_dir = dirname if dirname else "."
            suggestions.append({
                "command": f"go test ./{pkg_dir}/...",
                "reason": "Run Go tests in package",
            })
        elif ext == ".rs":
            suggestions.append({
                "command": f"cargo test {basename}",
                "reason": "Run related Rust tests",
            })
        elif ext == ".rb":
            suggestions.append({
                "command": f"bundle exec rspec --pattern '*{basename}*'",
                "reason": "Run related Ruby specs",
            })
        elif ext == ".java":
            suggestions.append({
                "command": f"mvn test -Dtest={basename}",
                "reason": "Run related Java tests",
            })

        return suggestions

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
