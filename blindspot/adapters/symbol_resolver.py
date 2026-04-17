"""Symbol resolver — language-agnostic symbol lookup using deep index.

Primary path: query the SQLite deep index (symbols + refs tables) for
cross-file callers, impact, and hierarchy. Text scanning is kept only as
a supplement for files whose parser does not emit cross-file call edges
(templates, regex-based strategies, fallback languages).
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .language_syntax import get_syntax_for_file, LanguageSyntax
from .project_structure import ProjectStructure

logger = logging.getLogger(__name__)

# Extensions whose parsing strategies emit cross-file `pending_calls`,
# so their caller edges already live in the normalized `refs` table.
_INDEXED_CALLER_EXTENSIONS: Set[str] = {
    ".py", ".pyw",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".kt", ".kts",
    ".cs",
    ".dart",
    ".go",
}

# Generic lifecycle / action names that routinely collide with unrelated
# framework helpers, schema builders, and migration DSL methods. Scan-only
# fallback evidence for these names is weak unless it is backed by a typed
# index edge or owner-aware structural context.
_AMBIGUOUS_SCAN_SYMBOLS: Set[str] = {
    "__construct", "__invoke", "boot", "create", "delete", "destroy",
    "down", "edit", "get", "handle", "index", "list", "mount", "process",
    "render", "resolve", "run", "set", "show", "store", "update", "up",
}


def _has_indexed_caller_edges(rel_path: str) -> bool:
    """Return True when the file's language populates the refs table."""
    lower = rel_path.lower()
    if lower.endswith(".blade.php"):
        return False
    return os.path.splitext(lower)[1] in _INDEXED_CALLER_EXTENSIONS


class SymbolResolver:
    """Language-agnostic symbol resolution using deep index + file scanning.

    Primary path uses the SQLite `refs` table for cross-file callers.
    Files whose parser does not populate `refs` are supplemented via a
    narrow file scan scoped to that subset only.
    """

    def __init__(self, project_path: str, structure: ProjectStructure,
                 index_manager=None):
        self.project_path = project_path
        self.structure = structure
        self.index_manager = index_manager

    # ── Symbol lookup from deep index ─────────────────────────────

    def get_file_symbols(self, rel_path: str) -> Optional[Dict[str, Any]]:
        """Get all symbols in a file from deep index.

        Returns dict with keys: functions, methods, classes, imports, exports, etc.
        Returns None if file not in index.
        """
        if not self.index_manager:
            return None
        return self.index_manager.get_file_summary(rel_path)

    def get_symbol_info(
        self,
        rel_path: str,
        symbol_name: str,
        owner: Optional[str] = None,
    ) -> Optional[Dict]:
        """Find a specific symbol's info (line, end_line, type, signature).

        When ``owner`` is supplied (e.g. the enclosing class name), exact
        owner-qualified matches are preferred over bare name matches. This
        lets callers disambiguate identically-named methods such as
        ``User.save`` versus ``Order.save`` within the same file.
        """
        summary = self.get_file_symbols(rel_path)
        if not summary:
            return None

        exact_match: Optional[Tuple[str, Dict[str, Any]]] = None
        owner_match: Optional[Tuple[str, Dict[str, Any]]] = None
        suffix_match: Optional[Tuple[str, Dict[str, Any]]] = None

        owner_prefix = f"{owner}." if owner else None

        for pool_key in ("functions", "methods", "classes"):
            for item in summary.get(pool_key, []):
                name = item.get("name", "")
                if not name:
                    continue
                if name == symbol_name and exact_match is None:
                    exact_match = (pool_key, item)
                    continue
                if owner_prefix and name == f"{owner_prefix}{symbol_name}" and owner_match is None:
                    owner_match = (pool_key, item)
                    continue
                if name.endswith(f".{symbol_name}") and suffix_match is None:
                    suffix_match = (pool_key, item)

        chosen = owner_match or exact_match or suffix_match
        if not chosen:
            return None

        pool_key, item = chosen
        return {
            "name": item.get("name", ""),
            "type": pool_key.rstrip("es").rstrip("s"),
            "line": item.get("line"),
            "end_line": item.get("end_line"),
            "signature": item.get("signature", ""),
        }

    def _list_indexed_files(self) -> List[str]:
        """Get all files from the deep index."""
        if not self.index_manager:
            return []
        try:
            return self.index_manager.find_files("*")
        except Exception:
            return []

    def get_all_classes(self) -> List[Dict[str, Any]]:
        """Get all classes from deep index across the project."""
        if not self.index_manager:
            return []

        # Prefer single SQL pass when supported by the index manager.
        lister = getattr(self.index_manager, "list_all_classes", None)
        if callable(lister):
            try:
                return [
                    {
                        "name": row.get("name", ""),
                        "file": row.get("file", ""),
                        "line": row.get("line"),
                        "end_line": row.get("end_line"),
                        "signature": row.get("signature", ""),
                    }
                    for row in lister()
                ]
            except Exception as e:
                logger.debug("list_all_classes failed, falling back: %s", e)

        results = []
        try:
            for rel_path in self._list_indexed_files():
                summary = self.index_manager.get_file_summary(rel_path)
                if summary:
                    for cls in summary.get("classes", []):
                        results.append({
                            "name": cls.get("name", ""),
                            "file": rel_path,
                            "line": cls.get("line"),
                            "end_line": cls.get("end_line"),
                            "signature": cls.get("signature", ""),
                        })
        except Exception as e:
            logger.debug("Failed to get all classes: %s", e)
        return results

    # ── Cross-file reference finding (language-agnostic) ──────────

    def find_references(self, symbol: str, scope: str = "all",
                        context_filter: Optional[str] = None,
                        definition_file: Optional[str] = None,
                        owner: Optional[str] = None) -> Dict[str, Any]:
        """Find all files referencing a symbol.

        Primary path: query the deep index `refs` table for cross-file
        callers. This is O(log N) per callee and avoids re-reading source
        files during the query phase.

        Supplement path: scan only those source/template files whose
        language strategy does not populate the refs table (templates,
        regex-based strategies, fallback languages). This keeps behavior
        correct for Blade/Dart/Go/ObjC while keeping the hot path index-backed.

        Args:
            symbol: Symbol name to search for
            scope: Category to filter ('all', 'models', 'controllers', etc.)
            context_filter: Optional class/model name for context filtering.
                When supplied, index callers are restricted to symbols
                whose short_name starts with ``context_filter.``
            definition_file: Optional path of the file that defines the
                symbol. When supplied, only callees defined in that file
                are considered (receiver-awareness).

        Returns:
            Dict with references list, total count, and metadata.
        """
        results: Dict[str, Any] = {
            "symbol": symbol,
            "scope": scope,
            "references": [],
            "total": 0,
        }
        if context_filter:
            results["context_filter"] = context_filter

        aggregated: Dict[str, Dict[str, Any]] = {}
        index_query_available = False

        # ── 1. Index-backed caller lookup ─────────────────────────────
        find_callers = getattr(self.index_manager, "find_callers", None) \
            if self.index_manager is not None else None
        if callable(find_callers):
            index_query_available = True
            try:
                caller_rows = find_callers(
                    called_short_name=symbol,
                    called_file_path=definition_file,
                    owner=owner or context_filter,
                )
            except Exception as exc:
                logger.debug("find_callers failed for %s: %s", symbol, exc)
                caller_rows = []

            for row in caller_rows:
                caller_file = row.get("caller_file")
                if not caller_file:
                    continue
                if scope != "all" and not self._file_in_scope(caller_file, scope):
                    continue
                if self._should_skip_index_usage(
                    symbol=symbol,
                    caller_file=caller_file,
                    caller_short_name=str(row.get("caller_short_name") or ""),
                    owner=owner or context_filter,
                ):
                    continue
                self._append_index_usage(aggregated, row, symbol)

        # ── 2. Supplement with targeted text scan ─────────────────────
        for rel_path, fpath in self._scan_candidates(scope):
            if rel_path in aggregated:
                continue
            fname = os.path.basename(fpath)
            if not (self.structure.is_source_file(fname)
                    or self.structure.is_template_file(fname)):
                continue
            if index_query_available and _has_indexed_caller_edges(rel_path):
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if symbol not in content:
                continue

            syntax = get_syntax_for_file(fname)
            lines = content.split("\n")
            usages: List[Dict[str, Any]] = []
            category = self.structure.categorize_file(rel_path) or "other"

            for i, line in enumerate(lines, 1):
                if symbol not in line:
                    continue

                usage_type = syntax.classify_usage(line, symbol)
                if not usage_type:
                    continue

                if self._should_skip_scan_usage(
                    symbol=symbol,
                    usage_type=usage_type,
                    file_path=rel_path,
                    category=category,
                    context_filter=context_filter,
                ):
                    continue

                if context_filter and usage_type in ("method_call", "reference"):
                    if not self._line_has_context(lines, i, context_filter, syntax):
                        continue

                usages.append({
                    "line": i,
                    "type": usage_type,
                    "snippet": line.strip()[:120],
                })

            if usages:
                aggregated[rel_path] = {
                    "file": rel_path,
                    "category": category or "other",
                    "evidence_source": "scan",
                    "usages": usages,
                    "count": len(usages),
                }

        references = list(aggregated.values())
        references.sort(key=lambda r: r["file"])
        results["references"] = references
        results["total"] = sum(r["count"] for r in references)
        return results

    def _append_index_usage(
        self,
        aggregated: Dict[str, Dict[str, Any]],
        row: Dict[str, Any],
        symbol: str,
    ) -> None:
        """Add an index-sourced caller row into the aggregated results.

        Index-sourced edges are tagged ``method_call`` by default. The
        PHP strategy emits a synthetic ``__file_scope__`` caller for
        top-level scripts (procedural entry points, Laravel bootstrap
        hooks, WordPress-style front controllers); those edges carry
        real cross-file signal but should **not** compete head-to-head
        with regular in-method callers. They are retagged as
        ``module_script`` so ``_usage_weight`` can deprioritize them
        without erasing the evidence.
        """
        caller_file = row["caller_file"]
        caller_line = row.get("caller_line") or 0
        caller_short = row.get("caller_short_name") or ""

        snippet = self._read_line_snippet(caller_file, caller_line) or (
            f"{caller_short} calls {symbol}"
        )

        entry = aggregated.get(caller_file)
        if entry is None:
            category = self.structure.categorize_file(caller_file)
            entry = {
                "file": caller_file,
                "category": category or "other",
                "evidence_source": "index",
                "usages": [],
                "count": 0,
            }
            aggregated[caller_file] = entry

        usage_type = "module_script" if caller_short == "__file_scope__" else "method_call"
        entry["usages"].append({
            "line": caller_line,
            "type": usage_type,
            "snippet": snippet,
            "in_symbol": caller_short,
        })
        entry["count"] = len(entry["usages"])

    def _line_has_context(self, lines: List[str], line_num: int,
                          context: str, syntax: LanguageSyntax) -> bool:
        """Check if a line is in the context of a specific class/variable."""
        # Check surrounding lines for context class name
        start = max(0, line_num - 5)
        end = min(len(lines), line_num + 3)
        window = "\n".join(lines[start:end])
        return context in window

    def _scan_candidates(self, scope: str) -> List[Tuple[str, str]]:
        """Return candidate (rel_path, abs_path) pairs for a given scope."""
        if scope == "all":
            return list(self.structure.walk_source_files())

        rel_dir = self.structure.get_rel_dir(scope)
        scan_dirs = [rel_dir] if rel_dir else ["."]
        collected: List[Tuple[str, str]] = []
        for scan_dir in scan_dirs:
            full_dir = os.path.join(self.project_path, scan_dir)
            if not os.path.isdir(full_dir):
                continue
            for root, dirs, files in os.walk(full_dir):
                dirs[:] = [d for d in dirs if not self.structure.should_exclude(d)]
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, self.project_path)
                    collected.append((rel_path, abs_path))
        return collected

    def _file_in_scope(self, rel_path: str, scope: str) -> bool:
        """Check if a file falls within a named scope/category."""
        if scope == "all":
            return True
        rel_dir = self.structure.get_rel_dir(scope)
        if rel_dir:
            norm = rel_path.replace(os.sep, "/")
            prefix = rel_dir.replace(os.sep, "/").rstrip("/") + "/"
            if norm.startswith(prefix):
                return True
        category = self.structure.categorize_file(rel_path)
        return category == scope

    def _read_line_snippet(self, rel_path: str, line_number: int,
                           max_len: int = 120) -> Optional[str]:
        """Read a single line from the project file for snippet display."""
        if not line_number or line_number <= 0:
            return None
        abs_path = os.path.join(self.project_path, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f, 1):
                    if idx == line_number:
                        return line.strip()[:max_len]
        except Exception:
            return None
        return None

    # ── Class hierarchy (language-agnostic) ───────────────────────

    def get_class_hierarchy(self, class_name: str) -> Dict[str, Any]:
        """Build class hierarchy from deep index + file scanning.

        Works with any OOP language supported by the indexer. The
        definition is resolved via the SQLite deep index (parsed by
        language-specific strategies) and inheritance edges are
        enriched by pattern matching on source where necessary.

        Args:
            class_name: Class name to look up

        Returns:
            Dict with extends, implements, traits/mixins, extended_by, implemented_by
        """
        result = {
            "class_name": class_name,
            "file": None,
            "extends": None,
            "implements": [],
            "mixins": [],
            "extended_by": [],
            "implemented_by": [],
        }

        # Find the class definition
        class_file = None
        class_signature = None

        # Try deep index first (single SQL pass)
        if self.index_manager:
            lookup = getattr(self.index_manager, "find_symbols_by_short_name", None)
            if callable(lookup):
                try:
                    for row in lookup(class_name):
                        if row.get("type") != "class":
                            continue
                        class_file = row.get("file")
                        class_signature = row.get("signature", "")
                        break
                except Exception as e:
                    logger.debug("find_symbols_by_short_name failed: %s", e)

            if class_file is None:
                try:
                    for rel_path in self._list_indexed_files():
                        summary = self.index_manager.get_file_summary(rel_path)
                        if not summary:
                            continue
                        for cls in summary.get("classes", []):
                            if cls.get("name") == class_name:
                                class_file = rel_path
                                class_signature = cls.get("signature", "")
                                break
                        if class_file:
                            break
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

        # Parse extends/implements from signature or file content
        if class_file:
            result["file"] = class_file
            full_path = os.path.join(self.project_path, class_file)
            syntax = get_syntax_for_file(class_file)

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                # Find extends
                if syntax.extends_keyword:
                    pattern = rf'class\s+{re.escape(class_name)}\s+{re.escape(syntax.extends_keyword)}\s+(\w+)'
                    m = re.search(pattern, content)
                    if m:
                        result["extends"] = m.group(1)
                elif syntax.name == "python":
                    # Python: class Foo(Bar, Baz)
                    m = re.search(rf'class\s+{re.escape(class_name)}\s*\(([^)]+)\)', content)
                    if m:
                        bases = [b.strip() for b in m.group(1).split(",")]
                        if bases:
                            result["extends"] = bases[0]
                            result["mixins"] = bases[1:] if len(bases) > 1 else []

                # Find implements (PHP, TypeScript, Java)
                if syntax.implements_keyword:
                    pattern = rf'class\s+{re.escape(class_name)}[^{{]*{re.escape(syntax.implements_keyword)}\s+([^{{]+)'
                    m = re.search(pattern, content)
                    if m:
                        result["implements"] = [i.strip() for i in m.group(1).split(",")]

                # Find traits/mixins (PHP: use TraitName;)
                if syntax.name == "php":
                    class_body = self._extract_class_body(content, class_name)
                    if class_body:
                        for m in re.finditer(r'\buse\s+([\w\\]+(?:\s*,\s*[\w\\]+)*)\s*;', class_body):
                            for trait in m.group(1).split(","):
                                trait = trait.strip().split("\\")[-1]
                                if trait not in ("HasFactory", "Notifiable", "SoftDeletes"):
                                    result["mixins"].append(trait)

            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Find who extends/implements this class
        source_files = self.structure.walk_source_files()
        for rel_path, abs_path in source_files:
            if rel_path == class_file:
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                if class_name not in content:
                    continue

                syntax = get_syntax_for_file(rel_path)

                # Check extends
                if syntax.extends_keyword:
                    if re.search(rf'\b{re.escape(syntax.extends_keyword)}\s+{re.escape(class_name)}\b', content):
                        # Find the child class name
                        m = re.search(rf'class\s+(\w+)\s+{re.escape(syntax.extends_keyword)}\s+{re.escape(class_name)}\b', content)
                        if m:
                            result["extended_by"].append({
                                "class": m.group(1), "file": rel_path
                            })
                elif syntax.name == "python":
                    if re.search(rf'class\s+(\w+)\s*\([^)]*\b{re.escape(class_name)}\b', content):
                        m = re.search(rf'class\s+(\w+)\s*\([^)]*\b{re.escape(class_name)}\b', content)
                        if m:
                            result["extended_by"].append({
                                "class": m.group(1), "file": rel_path
                            })

                # Check implements
                if syntax.implements_keyword:
                    if re.search(rf'\b{re.escape(syntax.implements_keyword)}\s+[^{{]*\b{re.escape(class_name)}\b', content):
                        m = re.search(rf'class\s+(\w+)[^{{]*{re.escape(syntax.implements_keyword)}\s+[^{{]*\b{re.escape(class_name)}\b', content)
                        if m:
                            result["implemented_by"].append({
                                "class": m.group(1), "file": rel_path
                            })

            except Exception:
                continue

        result["status"] = "success"
        return result

    def _extract_class_body(self, content: str, class_name: str) -> Optional[str]:
        """Extract the body of a class using brace counting."""
        pattern = rf'class\s+{re.escape(class_name)}\b[^{{]*\{{'
        m = re.search(pattern, content)
        if not m:
            return None

        start = m.end()
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
            pos += 1
        return content[start:pos - 1] if depth == 0 else None

    # ── Impact analysis (language-agnostic) ───────────────────────

    def get_impact_analysis(self, rel_path: str) -> Dict[str, Any]:
        """Analyze what would be affected if a file is modified.

        Finds all symbols defined in the file, then searches for references
        to those symbols across the codebase.

        Args:
            rel_path: Relative file path to analyze

        Returns:
            Dict with symbols and their cross-file references.
        """
        full_path = os.path.join(self.project_path, rel_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {rel_path}"}

        # Get symbols from deep index
        symbols_to_check = []
        summary = self.get_file_symbols(rel_path)
        if summary:
            for pool in ("classes", "functions", "methods"):
                for item in summary.get(pool, []):
                    name = item.get("name", "")
                    # Use short name (last part after .)
                    short = name.split(".")[-1] if "." in name else name
                    if short and len(short) > 2:  # Skip very short names
                        symbols_to_check.append(short)
        else:
            # Fallback: extract class/function names from file content
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                syntax = get_syntax_for_file(rel_path)
                for cls in syntax.find_class_declarations(content):
                    symbols_to_check.append(cls["name"])
                for fn in syntax.find_function_declarations(content):
                    symbols_to_check.append(fn["name"])
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        if not symbols_to_check:
            return {
                "status": "success",
                "file": rel_path,
                "symbols_checked": 0,
                "impacts": [],
                "message": "No symbols found to analyze",
            }

        # Find references for each symbol
        impacts = []
        affected_files: Set[str] = set()

        for symbol in symbols_to_check:
            refs = self.find_references(symbol, definition_file=rel_path)
            # Exclude self-references
            external_refs = [
                r for r in refs.get("references", [])
                if r["file"] != rel_path
            ]
            if external_refs:
                total = sum(r["count"] for r in external_refs)
                file_list = [r["file"] for r in external_refs]
                affected_files.update(file_list)
                impacts.append({
                    "symbol": symbol,
                    "referenced_in": len(external_refs),
                    "total_usages": total,
                    "files": file_list[:10],  # Cap at 10
                })

        return {
            "status": "success",
            "file": rel_path,
            "symbols_checked": len(symbols_to_check),
            "impacts": impacts,
            "total_affected_files": len(affected_files),
            "risk_level": self._assess_risk(len(affected_files)),
        }

    # ── Ripple effect (language-agnostic) ─────────────────────────

    def get_ripple_effect(self, rel_path: str, symbol: str,
                          change_type: str = "modify") -> Dict[str, Any]:
        """Trace the full ripple effect of changing a symbol.

        Language-agnostic version: uses deep index + file scanning + config
        scan_dirs for categorization instead of hardcoded Laravel paths.

        Args:
            rel_path: File containing the symbol
            symbol: Symbol name
            change_type: 'modify', 'rename', or 'delete'

        Returns:
            Categorized impacts with risk level.
        """
        refs = self.find_references(symbol, definition_file=rel_path)
        external_refs = [
            r for r in refs.get("references", [])
            if r["file"] != rel_path
        ]

        # Categorize impacts by directory category
        categorized: Dict[str, List[Dict]] = {}
        for ref in external_refs:
            cat = ref.get("category", "other")
            if cat not in categorized:
                categorized[cat] = []
            categorized[cat].append({
                "file": ref["file"],
                "usages": ref["usages"][:5],  # Cap usages per file
                "count": ref["count"],
            })

        total_files = len(external_refs)
        total_usages = sum(r["count"] for r in external_refs)
        weighted = self._compute_weighted_risk(external_refs, change_type=change_type)

        # Build summary
        result = {
            "status": "success",
            "file": rel_path,
            "symbol": symbol,
            "change_type": change_type,
            "impacts_by_category": categorized,
            "summary": {
                "total_files_affected": total_files,
                "total_usages": total_usages,
                "categories_affected": list(categorized.keys()),
                "risk_level": weighted["risk_level"],
                "risk_score": weighted["risk_score"],
                "weighted_model": "ripple-v2",
                "high_risk_impact_count": len(weighted["high_risk_impacts"]),
                "high_risk_impacts": weighted["high_risk_impacts"][:15],
            },
        }

        # Add symbol info if available
        sym_info = self.get_symbol_info(rel_path, symbol)
        if sym_info:
            result["symbol_info"] = sym_info

        return result

    # ── Project snapshot (language-agnostic) ──────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """Generate a compact snapshot of the entire project.

        Uses deep index for rich data, falls back to file scanning.

        Returns:
            Dict with metrics, classes, hotspots, and cross-references.
        """
        snapshot: Dict[str, Any] = {
            "status": "success",
            "language": self.structure.language,
            "framework": self.structure.framework,
        }

        # Metrics: count files by category and extension
        metrics: Dict[str, int] = {"total_files": 0}
        ext_counts: Dict[str, int] = {}
        category_counts: Dict[str, int] = {}
        all_files: List[Tuple[str, str]] = []

        for rel_path, abs_path in self.structure.walk_source_files():
            metrics["total_files"] += 1
            ext = os.path.splitext(rel_path)[1]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            cat = self.structure.categorize_file(rel_path)
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
            all_files.append((rel_path, abs_path))

        metrics["by_extension"] = ext_counts
        metrics["by_category"] = category_counts
        snapshot["metrics"] = metrics

        # Classes from deep index.
        #
        # Fast path: two single-SQL queries (``list_all_classes`` +
        # ``list_class_method_counts``) replace the per-file
        # ``get_file_summary`` loop that previously issued one query per
        # indexed file. On a 9k-file repo this cuts the snapshot from
        # ~8s to sub-second without changing the output shape.
        # FP guard: the method-count lookup keys on ``(file, class)``,
        # so classes with the same name in different files stay distinct.
        classes_summary = []
        if self.index_manager:
            try:
                lister = getattr(self.index_manager, "list_all_classes", None)
                counts_fn = getattr(self.index_manager, "list_class_method_counts", None)
                if callable(lister) and callable(counts_fn):
                    method_counts = counts_fn()
                    for cls in lister():
                        cls_name = cls.get("name", "")
                        rel_path = cls.get("file", "")
                        cls_info = {
                            "name": cls_name,
                            "file": rel_path,
                            "methods": method_counts.get((rel_path, cls_name), 0),
                            "category": self.structure.categorize_file(rel_path),
                        }
                        sig = cls.get("signature") or ""
                        if sig:
                            cls_info["signature"] = sig[:100]
                        classes_summary.append(cls_info)
                else:
                    # Legacy fallback; kept so non-SQLite managers still work.
                    for rel_path in self._list_indexed_files():
                        summary = self.index_manager.get_file_summary(rel_path)
                        if not summary:
                            continue
                        for cls in summary.get("classes", []):
                            cls_name = cls.get("name", "")
                            cls_method_count = sum(
                                1 for m in summary.get("methods", [])
                                if m.get("name", "").startswith(cls_name + ".")
                            )
                            cls_method_count += sum(
                                1 for f in summary.get("functions", [])
                                if f.get("name", "").startswith(cls_name + ".")
                            )
                            cls_info = {
                                "name": cls_name,
                                "file": rel_path,
                                "methods": cls_method_count,
                                "category": self.structure.categorize_file(rel_path),
                            }
                            if cls.get("signature"):
                                cls_info["signature"] = cls["signature"][:100]
                            classes_summary.append(cls_info)
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        snapshot["classes"] = classes_summary[:100]  # Cap at 100

        # Hotspots: files with most symbols or most lines.
        # Fast path: fetch every file's symbol count in one SQL pass
        # instead of issuing one ``get_file_summary`` query per file.
        symbol_counts_lookup: Dict[str, int] = {}
        if self.index_manager:
            counts_fn = getattr(self.index_manager, "list_file_symbol_counts", None)
            if callable(counts_fn):
                try:
                    symbol_counts_lookup = counts_fn()
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

        hotspots = []
        for rel_path, abs_path in all_files:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    line_count = sum(1 for _ in f)
            except Exception:
                line_count = 0

            symbol_count = symbol_counts_lookup.get(rel_path, 0)
            if not symbol_count and self.index_manager and not symbol_counts_lookup:
                try:
                    s = self.index_manager.get_file_summary(rel_path)
                    if s:
                        symbol_count = (
                            len(s.get("functions", [])) +
                            len(s.get("methods", [])) +
                            len(s.get("classes", []))
                        )
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

            if line_count > 200 or symbol_count > 10:
                hotspots.append({
                    "file": rel_path,
                    "lines": line_count,
                    "symbols": symbol_count,
                    "category": self.structure.categorize_file(rel_path),
                })

        hotspots.sort(key=lambda x: x["lines"], reverse=True)
        snapshot["hotspots"] = hotspots[:20]

        # Cross-references: which files import from which.
        #
        # Fast path: one SQL query returns imports for every indexed
        # file at once, eliminating the per-file ``get_file_summary``
        # loop that was the primary snapshot bottleneck on large repos.
        cross_refs: Dict[str, List[str]] = {}
        if self.index_manager:
            try:
                imports_fn = getattr(self.index_manager, "list_file_imports", None)
                if callable(imports_fn):
                    all_imports = imports_fn()
                    for rel_path, imports in all_imports.items():
                        if imports:
                            cross_refs[rel_path] = imports[:10]
                else:
                    for rel_path in self._list_indexed_files():
                        summary = self.index_manager.get_file_summary(rel_path)
                        if not summary:
                            continue
                        imports = summary.get("imports", [])
                        if imports:
                            cross_refs[rel_path] = imports[:10]
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        snapshot["import_graph"] = dict(list(cross_refs.items())[:50])

        return snapshot

    # ── Context for edit (language-agnostic) ──────────────────────

    def get_context_for_edit(self, rel_path: str,
                              symbol: Optional[str] = None) -> Dict[str, Any]:
        """Auto-gather all context needed before editing a file.

        Language-agnostic version: uses deep index for symbol info,
        config scan_dirs for file categorization, and cross-file references.

        Args:
            rel_path: File about to be edited
            symbol: Optional symbol to focus on

        Returns:
            Dict with file context, symbol info, hierarchy, references, and ripple.
        """
        full_path = os.path.join(self.project_path, rel_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {rel_path}"}

        context: Dict[str, Any] = {
            "status": "success",
            "file": rel_path,
            "category": self.structure.categorize_file(rel_path) or "other",
            "language": self.structure.language,
        }

        # File summary from deep index
        summary = self.get_file_symbols(rel_path)
        if summary:
            context["file_summary"] = {
                "classes": [c.get("name") for c in summary.get("classes", [])],
                "functions": [f.get("name") for f in summary.get("functions", [])],
                "methods": [m.get("name") for m in summary.get("methods", [])],
                "imports": summary.get("imports", [])[:20],
                "line_count": summary.get("line_count", 0),
            }

            # Class hierarchy for any classes in this file
            for cls in summary.get("classes", []):
                cls_name = cls.get("name", "")
                if cls_name:
                    hierarchy = self.get_class_hierarchy(cls_name)
                    if hierarchy.get("extends") or hierarchy.get("extended_by"):
                        context["class_hierarchy"] = hierarchy
                        break

        # Symbol-specific context
        if symbol:
            sym_info = self.get_symbol_info(rel_path, symbol)
            if sym_info:
                context["symbol_info"] = sym_info

                # Read symbol code
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = list(f)
                    start = sym_info.get("line", 1) - 1
                    end = sym_info.get("end_line", start + 20)
                    context["symbol_code"] = "".join(lines[start:end])
                except Exception as e:
                    logger.debug("Suppressed exception in best-effort path: %s", e)

            # Ripple effect for this symbol
            ripple = self.get_ripple_effect(rel_path, symbol)
            if ripple.get("status") == "success":
                context["ripple_effect"] = {
                    "risk_level": ripple["summary"]["risk_level"],
                    "total_files": ripple["summary"]["total_files_affected"],
                    "categories": ripple["summary"]["categories_affected"],
                    "top_files": [
                        ref["file"]
                        for cat_refs in ripple["impacts_by_category"].values()
                        for ref in cat_refs
                    ][:10],
                }

        # Impact analysis (who references this file's symbols)
        impact = self.get_impact_analysis(rel_path)
        if impact.get("impacts"):
            context["impact_summary"] = {
                "total_affected": impact["total_affected_files"],
                "risk_level": impact["risk_level"],
                "top_symbols": [
                    {"symbol": i["symbol"], "files": i["files"]}
                    for i in impact["impacts"][:5]
                ],
            }

        return context

    @staticmethod
    def _is_ambiguous_scan_symbol(symbol: str) -> bool:
        return (symbol or "").strip().lower() in _AMBIGUOUS_SCAN_SYMBOLS

    def _should_skip_scan_usage(
        self,
        symbol: str,
        usage_type: str,
        file_path: str,
        category: str,
        context_filter: Optional[str],
    ) -> bool:
        """Decide whether a scan-only usage is too weak to trust.

        False-positive notes:
            - Generic action names such as ``index`` or ``up`` often match
              migration/schema DSL methods or framework boilerplate. Those
              matches are not safe enough to surface as direct callers.
            - Weak text matches inside migration/bootstrap/config files can
              outnumber real callers in large Laravel/Symfony repos.

        False-negative notes:
            - This intentionally drops some scan-only recall for generic
              symbols when we cannot prove receiver identity. Index-backed
              edges remain unaffected and should carry the real callers.
        """
        usage_type = (usage_type or "reference").lower()
        category = (category or "other").lower()
        normalized = (file_path or "").replace("\\", "/").lower()

        if not self._is_ambiguous_scan_symbol(symbol):
            return False

        if category == "migrations" and usage_type in {"method_call", "reference"}:
            return True

        if usage_type == "reference" and category in {"tests", "docs", "views"} and not context_filter:
            return True

        if usage_type in {"method_call", "reference"} and not context_filter:
            if normalized.startswith("database/migrations/") or normalized.startswith("migrations/"):
                return True
            if normalized.startswith("bootstrap/") or normalized.startswith("config/"):
                return True

        return False

    def _should_skip_index_usage(
        self,
        symbol: str,
        caller_file: str,
        caller_short_name: str,
        owner: Optional[str],
    ) -> bool:
        """Filter weak index-backed collisions for generic symbol names.

        False positives:
            - Normalized refs can still collide on bare method names when a
              language strategy cannot recover the receiver type. Laravel
              migrations are the most common example: ``$table->index(...)``
              inside ``up()`` should not count as a caller of
              ``HomeController.index``.
        """
        if owner or not self._is_ambiguous_scan_symbol(symbol):
            return False

        category = (self.structure.categorize_file(caller_file) or "other").lower()
        caller_short = (caller_short_name or "").lower()
        if category == "migrations" and caller_short in {"up", "down", "change"}:
            return True
        return False

    def get_symbol_change_context(
        self,
        rel_path: str,
        symbol: str,
        change_type: str = "modify",
        max_related: int = 10,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a richer change-impact context for a symbol.

        This is the higher-signal view used by the public context engine:
        direct callers, indirect dependents, blast radius, risk reasons,
        and safe edit guidance. ``owner`` disambiguates identically
        named methods across multiple classes in the same file.
        """
        ripple = self.get_ripple_effect(rel_path, symbol, change_type=change_type)
        if ripple.get("status") != "success":
            return ripple

        symbol_info = self.get_symbol_info(rel_path, symbol, owner=owner)
        effective_owner = owner
        context_filter = owner
        if symbol_info and not effective_owner:
            raw_name = str(symbol_info.get("name", ""))
            if "." in raw_name:
                context_filter = raw_name.split(".", 1)[0]
                effective_owner = context_filter

        direct_refs = [
            ref for ref in self.find_references(
                symbol,
                context_filter=context_filter,
                definition_file=rel_path,
                owner=effective_owner,
            ).get("references", [])
            if ref.get("file") != rel_path
        ]
        direct_refs.sort(
            key=lambda ref: (
                self._category_weight(str(ref.get("category", "other"))),
                # Strongest usage weight across this ref's call sites.
                # Ensures a file whose only evidence is a ``module_script``
                # (synthetic file-scope) edge ranks below any file with
                # at least one real in-method caller, even when categories
                # tie. The previous sort ignored usage type, causing
                # procedural PHP entry-points to crowd the top of
                # ``direct_callers`` on Laravel/WordPress-style repos.
                max(
                    (
                        self._usage_weight(str(u.get("type", "reference")))
                        for u in (ref.get("usages") or [])
                        if isinstance(u, dict)
                    ),
                    default=1.0,
                ),
                int(ref.get("count", 0)),
            ),
            reverse=True,
        )
        direct_callers = self._summarize_direct_callers(direct_refs, max_related=max_related)
        indirect_dependents = self._find_indirect_dependents(
            origin_file=rel_path,
            direct_callers=direct_callers,
            max_related=max_related,
        )
        blast_radius = self._build_blast_radius(
            direct_refs=direct_refs,
            direct_callers=direct_callers,
            indirect_dependents=indirect_dependents,
            change_type=change_type,
        )
        risk_reasons = self._build_risk_reasons(
            rel_path=rel_path,
            direct_refs=direct_refs,
            direct_callers=direct_callers,
            indirect_dependents=indirect_dependents,
            blast_radius=blast_radius,
            ripple=ripple,
        )
        safe_edit_hints = self._build_safe_edit_hints(
            rel_path=rel_path,
            symbol=symbol,
            direct_callers=direct_callers,
            indirect_dependents=indirect_dependents,
            ripple=ripple,
        )

        return {
            "status": "success",
            "file": rel_path,
            "symbol": symbol,
            "canonical_symbol": symbol_info.get("name", symbol) if symbol_info else symbol,
            "change_type": change_type,
            "direct_callers": direct_callers,
            "indirect_dependents": indirect_dependents,
            "blast_radius": blast_radius,
            "risk_reasons": risk_reasons,
            "safe_edit_hints": safe_edit_hints,
            "ripple_effect": ripple,
        }

    # ── Helpers ───────────────────────────────────────────────────

    def _summarize_direct_callers(
        self,
        refs: List[Dict[str, Any]],
        max_related: int = 10,
    ) -> List[Dict[str, Any]]:
        """Convert raw reference hits into agent-friendly direct caller summaries."""
        direct_callers: List[Dict[str, Any]] = []
        for ref in refs[:max_related]:
            usages = ref.get("usages", []) if isinstance(ref.get("usages"), list) else []
            usage_types = sorted(
                {
                    str(usage.get("type", "reference"))
                    for usage in usages
                    if isinstance(usage, dict)
                }
            )
            strongest_usage = "reference"
            strongest_score = -1.0
            for usage_type in usage_types or ["reference"]:
                score = self._usage_weight(usage_type)
                if score > strongest_score:
                    strongest_usage = usage_type
                    strongest_score = score

            snippets = [
                {
                    "line": int(usage.get("line", 0)),
                    "type": str(usage.get("type", "reference")),
                    "snippet": str(usage.get("snippet", "")),
                }
                for usage in usages[:3]
                if isinstance(usage, dict)
            ]
            direct_callers.append(
                {
                    "file": ref.get("file", ""),
                    "category": ref.get("category", "other"),
                    "count": int(ref.get("count", 0)),
                    "evidence_source": ref.get("evidence_source", "scan"),
                    "strongest_usage": strongest_usage,
                    "usage_types": usage_types or ["reference"],
                    "snippets": snippets,
                }
            )
        return direct_callers

    def _find_indirect_dependents(
        self,
        origin_file: str,
        direct_callers: List[Dict[str, Any]],
        max_related: int = 10,
    ) -> List[Dict[str, Any]]:
        """Approximate second-order dependents by expanding direct caller files."""
        indirect: Dict[str, Dict[str, Any]] = {}
        direct_files = {item.get("file") for item in direct_callers if item.get("file")}

        # Cap expansion to keep context cheap and responsive.
        for caller in direct_callers[: min(6, len(direct_callers))]:
            caller_file = str(caller.get("file", ""))
            if not caller_file:
                continue
            impact = self.get_impact_analysis(caller_file)
            if impact.get("status") != "success":
                continue

            for item in impact.get("impacts", []):
                for dependent_file in item.get("files", []) or []:
                    if (
                        not dependent_file
                        or dependent_file == origin_file
                        or dependent_file in direct_files
                    ):
                        continue

                    if dependent_file not in indirect:
                        indirect[dependent_file] = {
                            "file": dependent_file,
                            "category": self.structure.categorize_file(dependent_file) or "other",
                            "via": [],
                            "symbols": set(),
                        }
                    indirect[dependent_file]["via"].append(caller_file)
                    symbol_name = item.get("symbol")
                    if symbol_name:
                        indirect[dependent_file]["symbols"].add(str(symbol_name))

        ranked = []
        for item in indirect.values():
            via_files = sorted(set(item["via"]))
            symbols = sorted(item["symbols"])
            ranked.append(
                {
                    "file": item["file"],
                    "category": item["category"],
                    "via": via_files[:3],
                    "symbols": symbols[:5],
                    "count": len(via_files),
                }
            )

        ranked.sort(
            key=lambda item: (
                self._category_weight(str(item.get("category", "other"))),
                int(item.get("count", 0)),
            ),
            reverse=True,
        )
        return ranked[:max_related]

    def _build_blast_radius(
        self,
        direct_refs: List[Dict[str, Any]],
        direct_callers: List[Dict[str, Any]],
        indirect_dependents: List[Dict[str, Any]],
        change_type: str = "modify",
    ) -> Dict[str, Any]:
        """Build a compact blast-radius summary for an agent."""
        weighted = self._compute_weighted_risk(direct_refs, change_type=change_type)
        direct_files = len(direct_callers)
        indirect_files = len(indirect_dependents)
        total_usages = sum(int(ref.get("count", 0)) for ref in direct_refs)
        categories = sorted(
            {
                str(item.get("category", "other"))
                for item in direct_callers + indirect_dependents
                if item.get("category")
            }
        )
        combined_score = round(
            float(weighted.get("risk_score", 0.0)) + indirect_files * 0.9 + len(categories) * 0.5,
            2,
        )
        if combined_score >= 35.0:
            level = "critical"
        elif combined_score >= 18.0:
            level = "high"
        elif combined_score >= 8.0:
            level = "medium"
        else:
            level = "low"

        return {
            "risk_level": level,
            "risk_score": combined_score,
            "direct_files": direct_files,
            "direct_usages": total_usages,
            "indirect_files": indirect_files,
            "total_files": direct_files + indirect_files,
            "categories": categories,
            "high_risk_files": weighted.get("high_risk_impacts", [])[:5],
        }

    def _build_risk_reasons(
        self,
        rel_path: str,
        direct_refs: List[Dict[str, Any]],
        direct_callers: List[Dict[str, Any]],
        indirect_dependents: List[Dict[str, Any]],
        blast_radius: Dict[str, Any],
        ripple: Dict[str, Any],
    ) -> List[str]:
        """Turn structural risk into short human-readable reasons."""
        reasons: List[str] = []
        direct_files = int(blast_radius.get("direct_files", 0))
        indirect_files = int(blast_radius.get("indirect_files", 0))
        categories = blast_radius.get("categories", []) or []

        if direct_files >= 8:
            reasons.append(f"Symbol has broad direct usage across {direct_files} files.")
        elif direct_files >= 3:
            reasons.append(f"Symbol is used directly in {direct_files} files.")

        if indirect_files:
            reasons.append(
                f"Second-order dependency chain detected through {indirect_files} indirect dependents."
            )

        usage_types = {
            str(usage.get("type", "reference"))
            for ref in direct_refs
            for usage in ref.get("usages", []) or []
            if isinstance(usage, dict)
        }
        if "extends_or_implements" in usage_types:
            reasons.append("Inheritance or interface contracts depend on this symbol.")
        if "instantiation" in usage_types:
            reasons.append("Construction paths depend on this symbol's API shape.")
        if "static_call" in usage_types:
            reasons.append("Static call sites depend on the symbol name and signature.")
        if "method_call" in usage_types:
            reasons.append("Runtime method call sites depend on current behavior.")

        if any(cat in categories for cat in ("controllers", "routes", "middleware")):
            reasons.append("Request/entry-point code depends on this change.")
        if any(cat in categories for cat in ("models", "services")):
            reasons.append("Core domain code is in the dependency path.")
        if any("test" in str(item.get("file", "")).lower() for item in direct_callers + indirect_dependents):
            reasons.append("Tests reference the affected path and should be re-run.")

        sensitive_paths = [ref.get("file", "") for ref in direct_refs if any(
            token in str(ref.get("file", "")).lower() for token in ("auth", "payment", "webhook")
        )]
        if sensitive_paths:
            reasons.append("Sensitive auth/payment/webhook paths are affected.")

        if ripple.get("summary", {}).get("high_risk_impact_count", 0):
            reasons.append("Weighted ripple model flagged high-risk downstream files.")

        if not reasons:
            reasons.append(f"Change appears localized to {rel_path} with limited downstream coupling.")

        return reasons[:6]

    def _build_safe_edit_hints(
        self,
        rel_path: str,
        symbol: str,
        direct_callers: List[Dict[str, Any]],
        indirect_dependents: List[Dict[str, Any]],
        ripple: Dict[str, Any],
    ) -> List[str]:
        """Generate signal-specific hints that improve agent edit quality.

        Only hints grounded in actual relationships are emitted; generic
        reminders (e.g. ``Read the symbol first``) are intentionally
        omitted to keep token usage low and the signal dense.
        """
        hints: List[str] = []

        if direct_callers:
            top_files = ", ".join(item["file"] for item in direct_callers[:3])
            hints.append(f"Inspect the top direct callers first: {top_files}.")

        usage_types = {
            usage_type
            for caller in direct_callers
            for usage_type in caller.get("usage_types", [])
        }
        if "extends_or_implements" in usage_types:
            hints.append("Preserve the public contract unless you also update subclasses or implementations.")
        if "instantiation" in usage_types:
            hints.append("Avoid changing constructor shape or required initialization without updating call sites.")
        if "static_call" in usage_types or "method_call" in usage_types:
            hints.append("Keep the symbol name and call signature stable unless you intend a coordinated refactor.")

        if indirect_dependents:
            hints.append("Re-check files that depend on direct callers; they can break even if first-order callers compile.")

        if ripple.get("summary", {}).get("risk_level") in {"high", "critical"}:
            hints.append("Treat this as a broad change: make the edit in smaller steps and verify related files incrementally.")

        return hints[:6]

    @staticmethod
    def _category_weight(category: str) -> float:
        category = (category or "other").lower()
        weights = {
            "routes": 1.6,
            "controllers": 1.4,
            "middleware": 1.5,
            "models": 1.35,
            "services": 1.25,
            "views": 1.1,
            "tests": 0.7,
            "migrations": 1.55,
        }
        return float(weights.get(category, 1.0))

    @staticmethod
    def _usage_weight(usage_type: str) -> float:
        usage_type = (usage_type or "reference").lower()
        weights = {
            "extends_or_implements": 1.5,
            "instantiation": 1.3,
            "static_call": 1.25,
            "method_call": 1.2,
            "reference": 1.0,
            "type_hint": 0.95,
            "import": 0.85,
            # File-scope / top-level script callers (synthetic
            # ``__file_scope__`` symbol): real cross-file edges but
            # weaker than any in-method caller. Kept above 0 so they
            # still contribute to risk scoring.
            "module_script": 0.55,
        }
        return float(weights.get(usage_type, 1.0))

    @staticmethod
    def _change_type_weight(change_type: str) -> float:
        t = (change_type or "modify").lower()
        if t == "rename":
            return 1.15
        if t == "delete":
            return 1.35
        if t in {"signature_change", "contract_change"}:
            return 1.25
        return 1.0

    def _compute_weighted_risk(self, refs: List[Dict[str, Any]], change_type: str = "modify") -> Dict[str, Any]:
        """Compute a weighted risk score across category + usage + change type."""
        score = 0.0
        high_risk_impacts: List[Dict[str, Any]] = []
        ctype_weight = self._change_type_weight(change_type)

        for ref in refs:
            category = ref.get("category", "other")
            cat_weight = self._category_weight(category)
            usages = ref.get("usages", []) if isinstance(ref.get("usages", []), list) else []

            usage_score = 0.0
            for usage in usages:
                if isinstance(usage, dict):
                    usage_score += self._usage_weight(str(usage.get("type", "reference")))

            count = int(ref.get("count", 0))
            base_score = 1.0 + min(10, max(0, count)) * 0.35 + usage_score * 0.2
            file_score = base_score * cat_weight * ctype_weight

            file_path = str(ref.get("file", "")).lower()
            if any(k in file_path for k in ("auth", "payment", "webhook")):
                file_score *= 1.4

            file_score = round(file_score, 3)
            score += file_score
            if file_score >= 6.0:
                high_risk_impacts.append(
                    {
                        "file": ref.get("file", ""),
                        "category": category,
                        "score": file_score,
                        "count": count,
                    }
                )

        score = round(score, 2)
        if score >= 30.0:
            level = "critical"
        elif score >= 16.0:
            level = "high"
        elif score >= 7.0:
            level = "medium"
        else:
            level = "low"

        high_risk_impacts.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return {
            "risk_level": level,
            "risk_score": score,
            "high_risk_impacts": high_risk_impacts,
        }

    @staticmethod
    def _assess_risk(affected_files: int) -> str:
        """Assess risk level based on number of affected files."""
        if affected_files == 0:
            return "low"
        elif affected_files <= 3:
            return "low"
        elif affected_files <= 8:
            return "medium"
        elif affected_files <= 15:
            return "high"
        else:
            return "critical"
