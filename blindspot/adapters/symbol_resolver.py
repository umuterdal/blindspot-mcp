"""Symbol resolver — language-agnostic symbol lookup using deep index.

Uses the SQLite deep index (populated by tree-sitter parsers for 12+ languages)
to resolve symbols, find references, trace hierarchies, and analyze impact
without any hardcoded paths or language-specific logic.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .language_syntax import get_syntax_for_file, LanguageSyntax
from .project_structure import ProjectStructure

logger = logging.getLogger(__name__)


class SymbolResolver:
    """Language-agnostic symbol resolution using deep index + file scanning.

    All methods work with any language that has a tree-sitter parser.
    Falls back to regex-based scanning for unsupported languages.
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

    def get_symbol_info(self, rel_path: str, symbol_name: str) -> Optional[Dict]:
        """Find a specific symbol's info (line, end_line, type, signature)."""
        summary = self.get_file_symbols(rel_path)
        if not summary:
            return None

        for pool_key in ("functions", "methods", "classes"):
            for item in summary.get(pool_key, []):
                name = item.get("name", "")
                if name == symbol_name or name.endswith(f".{symbol_name}"):
                    return {
                        "name": name,
                        "type": pool_key.rstrip("es").rstrip("s"),
                        "line": item.get("line"),
                        "end_line": item.get("end_line"),
                        "signature": item.get("signature", ""),
                    }
        return None

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
                        context_filter: Optional[str] = None) -> Dict[str, Any]:
        """Find all files referencing a symbol using file scanning.

        Unlike the Laravel version which only scans .php files in hardcoded dirs,
        this scans ALL source files in configured scan dirs using the language
        syntax adapter for classification.

        Args:
            symbol: Symbol name to search for
            scope: Category to filter ('all', 'models', 'controllers', etc.)
            context_filter: Optional class/model name for context filtering

        Returns:
            Dict with references list, total count, and metadata.
        """
        results = {"symbol": symbol, "scope": scope, "references": [], "total": 0}
        if context_filter:
            results["context_filter"] = context_filter

        # Determine directories to scan
        if scope == "all":
            scan_dirs = self.structure.get_all_scan_dirs()
            if not scan_dirs:
                # No config — scan entire project
                scan_dirs = ["."]
        else:
            rel_dir = self.structure.get_rel_dir(scope)
            scan_dirs = [rel_dir] if rel_dir else ["."]

        seen_files: Set[str] = set()

        for scan_dir in scan_dirs:
            full_dir = os.path.join(self.project_path, scan_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, dirs, files in os.walk(full_dir):
                # Prune excluded dirs
                dirs[:] = [d for d in dirs if not self.structure.should_exclude(d)]

                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, self.project_path)

                    # Skip already-scanned files (overlapping scan dirs)
                    if rel_path in seen_files:
                        continue
                    seen_files.add(rel_path)

                    # Check if it's a source/template file
                    if not (self.structure.is_source_file(fname) or
                            self.structure.is_template_file(fname)):
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
                    usages = []

                    for i, line in enumerate(lines, 1):
                        if symbol not in line:
                            continue

                        usage_type = syntax.classify_usage(line, symbol)
                        if not usage_type:
                            continue

                        # Context filtering: skip if context_filter set and
                        # this line doesn't relate to the specified class
                        if context_filter and usage_type in ("method_call", "reference"):
                            if not self._line_has_context(
                                lines, i, context_filter, syntax
                            ):
                                continue

                        usages.append({
                            "line": i,
                            "type": usage_type,
                            "snippet": line.strip()[:120],
                        })

                    if usages:
                        category = self.structure.categorize_file(rel_path)
                        results["references"].append({
                            "file": rel_path,
                            "category": category or "other",
                            "usages": usages,
                            "count": len(usages),
                        })

        results["total"] = sum(r["count"] for r in results["references"])
        return results

    def _line_has_context(self, lines: List[str], line_num: int,
                          context: str, syntax: LanguageSyntax) -> bool:
        """Check if a line is in the context of a specific class/variable."""
        # Check surrounding lines for context class name
        start = max(0, line_num - 5)
        end = min(len(lines), line_num + 3)
        window = "\n".join(lines[start:end])
        return context in window

    # ── Class hierarchy (language-agnostic) ───────────────────────

    def get_class_hierarchy(self, class_name: str) -> Dict[str, Any]:
        """Build class hierarchy from deep index + file scanning.

        Works with any OOP language. Uses tree-sitter parsed data from
        the deep index for signature/extends info, falls back to regex.

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

        # Try deep index first
        if self.index_manager:
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
            except Exception:
                pass

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

            except Exception:
                pass

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
            except Exception:
                pass

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
            refs = self.find_references(symbol)
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
        refs = self.find_references(symbol)
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
                "risk_level": self._assess_risk(total_files),
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

        # Classes from deep index
        classes_summary = []
        if self.index_manager:
            try:
                for rel_path in self._list_indexed_files():
                    summary = self.index_manager.get_file_summary(rel_path)
                    if not summary:
                        continue
                    for cls in summary.get("classes", []):
                        cls_name = cls.get("name", "")
                        # Count methods belonging to this specific class
                        cls_method_count = sum(
                            1 for m in summary.get("methods", [])
                            if m.get("name", "").startswith(cls_name + ".")
                        )
                        # Also count from functions that have ClassName. prefix
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
            except Exception:
                pass

        snapshot["classes"] = classes_summary[:100]  # Cap at 100

        # Hotspots: files with most symbols or most lines
        hotspots = []
        for rel_path, abs_path in all_files:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    line_count = sum(1 for _ in f)
            except Exception:
                line_count = 0

            symbol_count = 0
            if self.index_manager:
                try:
                    s = self.index_manager.get_file_summary(rel_path)
                    if s:
                        symbol_count = (
                            len(s.get("functions", [])) +
                            len(s.get("methods", [])) +
                            len(s.get("classes", []))
                        )
                except Exception:
                    pass

            if line_count > 200 or symbol_count > 10:
                hotspots.append({
                    "file": rel_path,
                    "lines": line_count,
                    "symbols": symbol_count,
                    "category": self.structure.categorize_file(rel_path),
                })

        hotspots.sort(key=lambda x: x["lines"], reverse=True)
        snapshot["hotspots"] = hotspots[:20]

        # Cross-references: which files import from which
        cross_refs: Dict[str, List[str]] = {}
        if self.index_manager:
            try:
                for rel_path in self._list_indexed_files():
                    summary = self.index_manager.get_file_summary(rel_path)
                    if not summary:
                        continue
                    imports = summary.get("imports", [])
                    if imports:
                        cross_refs[rel_path] = imports[:10]
            except Exception:
                pass

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
                        lines = f.readlines()
                    start = sym_info.get("line", 1) - 1
                    end = sym_info.get("end_line", start + 20)
                    context["symbol_code"] = "".join(lines[start:end])
                except Exception:
                    pass

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
                    {"symbol": i["symbol"], "files": i["referenced_in"]}
                    for i in impact["impacts"][:5]
                ],
            }

        return context

    # ── Helpers ───────────────────────────────────────────────────

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
