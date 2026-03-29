"""
File Edit Service - Apply edits to project files without loading them into LLM context.

This service enables the LLM to modify files by:
1. Search-replace: Find exact strings and replace them (single or batch)
2. Search-replace with occurrence: Replace the Nth match when string isn't unique
3. Symbol-based replacement: Replace an entire function/method/class body
4. Line-range replacement: Replace a range of lines by line number
5. Region read: Get only the relevant portion of a file for context

The key benefit: the file content is read/written server-side, so the LLM
never needs to Read the full file into its context window.

Response strategy:
- Small diffs (≤30 lines): full unified diff returned
- Large diffs (>30 lines): compact summary with change stats only
- This keeps response size constant regardless of edit size
"""

import difflib
import glob as glob_module
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_service import BaseService
from ..indexing import get_index_manager

logger = logging.getLogger(__name__)

# Diff lines threshold — above this, return summary instead of full diff
DIFF_TRUNCATE_THRESHOLD = 30

# Maximum detail files to keep in .blindspot/output/
MAX_DETAIL_FILES = 20


class FileEditService(BaseService):
    """
    Service for applying edits to project files.

    Operates entirely server-side — the LLM sends instructions,
    this service reads the file, applies the edit, writes it back,
    and returns a compact response.
    """

    # ─── Path & IO ───────────────────────────────────────────────

    def _resolve_path(self, file_path: str) -> str:
        """Resolve a relative file path to an absolute path with symlink-safe security."""
        base = self.base_path
        if not base:
            from ..indexing import get_index_manager
            mgr = get_index_manager()
            base = mgr.project_path if mgr else None
        if not base:
            raise ValueError("Project path not set. Call set_project_path first.")

        full_path = os.path.normpath(os.path.join(base, file_path))
        # Resolve symlinks for real path comparison
        real_path = os.path.realpath(full_path)
        real_base = os.path.realpath(base)
        try:
            common = os.path.commonpath([real_path, real_base])
            if common != real_base:
                raise ValueError(f"Path traversal blocked: {file_path}")
        except ValueError:
            raise ValueError(f"Path traversal blocked: {file_path}")
        return full_path

    def _read_file(self, full_path: str) -> str:
        """Read file content with encoding fallback."""
        for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
            try:
                with open(full_path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode file: {full_path}")

    def _write_file(self, full_path: str, content: str) -> None:
        """Write content to file."""
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    # ─── Diff & Response ─────────────────────────────────────────

    def _make_diff(self, old: str, new: str, file_path: str, n: int = 3) -> str:
        """Generate a unified diff."""
        return "".join(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=n,
        ))

    def _save_detail_file(self, data: Dict[str, Any], tool_name: str) -> str:
        """Save full response data to a detail file for large outputs.

        Creates .blindspot/output/ directory in the project root and saves
        the full response as a JSON file with timestamp. Keeps only the
        last MAX_DETAIL_FILES files.

        Args:
            data: Full response dictionary to save.
            tool_name: Name of the tool that generated this output.

        Returns:
            Path to the saved detail file (relative to project root).
        """
        try:
            base = self.base_path
            if not base:
                return ""

            output_dir = os.path.join(base, ".blindspot", "output")
            os.makedirs(output_dir, exist_ok=True)

            # Generate timestamped filename
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{tool_name}_{ts}.json"
            full_path = os.path.join(output_dir, filename)

            with open(full_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            # Cleanup: keep only the last MAX_DETAIL_FILES
            existing = sorted(
                glob_module.glob(os.path.join(output_dir, "*.json")),
                key=os.path.getmtime,
            )
            if len(existing) > MAX_DETAIL_FILES:
                for old_file in existing[: len(existing) - MAX_DETAIL_FILES]:
                    try:
                        os.remove(old_file)
                    except OSError:
                        pass

            return os.path.relpath(full_path, base)
        except Exception as e:
            logger.debug("Failed to save detail file: %s", e)
            return ""

    def _build_response(
        self, old_content: str, new_content: str, file_path: str
    ) -> Dict[str, Any]:
        """
        Build a smart response based on diff size.
        Small diff → full diff. Large diff → summary only + detail file.
        """
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()

        diff_text = self._make_diff(old_content, new_content, file_path)
        diff_line_count = diff_text.count('\n')

        response = {
            "status": "success",
            "file_path": file_path,
            "lines_before": len(old_lines),
            "lines_after": len(new_lines),
        }

        if diff_line_count <= DIFF_TRUNCATE_THRESHOLD:
            # Small diff — return full diff (cheap)
            response["diff"] = diff_text
        else:
            # Large diff — return summary only (constant cost)
            added = sum(1 for l in diff_text.splitlines() if l.startswith('+') and not l.startswith('+++'))
            removed = sum(1 for l in diff_text.splitlines() if l.startswith('-') and not l.startswith('---'))

            # Find changed line ranges from hunk headers
            hunks = []
            for line in diff_text.splitlines():
                if line.startswith('@@'):
                    hunks.append(line.split('@@')[1].strip())

            response["diff_summary"] = {
                "total_diff_lines": diff_line_count,
                "lines_added": added,
                "lines_removed": removed,
                "hunks": hunks[:5],  # Max 5 hunk headers
            }
            response["hint"] = "Large diff truncated. Use get_edit_region to verify specific sections."

            # Save full diff to detail file
            detail_data = {
                "file_path": file_path,
                "diff": diff_text,
                "lines_before": len(old_lines),
                "lines_after": len(new_lines),
                "lines_added": added,
                "lines_removed": removed,
            }
            detail_path = self._save_detail_file(detail_data, "apply_edit")
            if detail_path:
                response["detail_file"] = detail_path

        return response

    # ─── Validation ──────────────────────────────────────────────

    def _check_syntax(self, full_path: str) -> Optional[str]:
        """
        Run syntax check on a file based on its language.
        Returns error message if syntax invalid, None if OK.
        Silently returns None if the checker binary is not found.

        Supported languages:
        - PHP: php -l
        - JavaScript/TypeScript: node --check / tsc --noEmit
        - Python: python -m py_compile
        - Go: go vet
        - Rust: cargo check
        """
        from ..config import get_config, EXTENSION_LANGUAGE_MAP, DEFAULT_SYNTAX_CHECKERS

        ext = os.path.splitext(full_path)[1]
        language = EXTENSION_LANGUAGE_MAP.get(ext)
        if not language:
            return None

        # Check for project-level config override
        try:
            base = self.base_path
            if base:
                config = get_config(base)
                cmd_template = config.get_syntax_checker(language)
            else:
                cmd_template = DEFAULT_SYNTAX_CHECKERS.get(language)
        except Exception:
            cmd_template = DEFAULT_SYNTAX_CHECKERS.get(language)

        if not cmd_template:
            return None

        # Safe command building — split template first, then substitute {file}
        # This correctly handles paths with spaces without shell=True
        import shlex
        try:
            cmd_parts = shlex.split(cmd_template)
        except ValueError:
            cmd_parts = cmd_template.split()
        # Replace {file} placeholder in each part — preserves path as single token
        cmd_parts = [part.replace("{file}", full_path) for part in cmd_parts]

        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                err = result.stdout.strip() or result.stderr.strip()
                # Clean up common noise
                lines = [l for l in err.splitlines()
                         if l and not l.startswith('Errors parsing')]
                return lines[0] if lines else err[:200]
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    # Keep backward compatibility
    def _check_php_syntax(self, full_path: str) -> Optional[str]:
        """Legacy method — delegates to _check_syntax."""
        return self._check_syntax(full_path)

    # ─── Symbol Lookup (shared) ──────────────────────────────────

    def _find_symbol_info(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Find a symbol's info from the index.
        Returns dict with status + start_line/end_line on success,
        or status + error message on failure.
        """
        index_manager = get_index_manager()
        summary = index_manager.get_file_summary(file_path)

        if not summary:
            return {
                "status": "error",
                "message": "File not in index. Run build_deep_index first.",
            }

        # Search functions → methods → classes
        for pool_key in ("functions", "methods", "classes"):
            for item in summary.get(pool_key, []):
                name = item.get("name", "")
                if name == symbol or name.endswith(f".{symbol}"):
                    start = item.get("line")
                    end = item.get("end_line")
                    if start is None:
                        return {
                            "status": "error",
                            "message": f"Symbol '{symbol}' found but line info missing",
                        }
                    # Fallback end_line if missing
                    if end is None:
                        end = start + (50 if pool_key == "classes" else 20)
                    return {
                        "status": "ok",
                        "start_line": start,
                        "end_line": end,
                        "type": pool_key.rstrip("es").rstrip("s"),  # functions→function
                    }

        # Not found — list available symbols
        available = []
        for pool_key in ("functions", "methods", "classes"):
            for item in summary.get(pool_key, []):
                available.append(item.get("name"))
        return {
            "status": "error",
            "message": f"Symbol '{symbol}' not found in {file_path}",
            "available_symbols": available,
        }

    # ─── Index Refresh ──────────────────────────────────────────

    def _refresh_file_index(self, rel_path: str, full_path: str) -> None:
        """Re-index a single file after edit to keep the deep index fresh.

        Called after a successful write (and PHP syntax check). Failures here
        are logged but never propagated — the edit itself already succeeded.
        """
        try:
            index_manager = get_index_manager()
            if not index_manager or not index_manager.index_builder:
                return

            builder = index_manager.index_builder
            if hasattr(builder, 'reindex_file'):
                builder.reindex_file(rel_path, full_path)
            else:
                logger.debug("Index builder has no reindex_file method; skipping refresh")
        except Exception as e:
            # Never fail the edit because of an index refresh issue
            logger.debug("Index refresh after edit failed (non-fatal): %s", e)

    # ─── apply_edit (main entry) ─────────────────────────────────

    def apply_edit(
        self,
        file_path: str,
        search: str = None,
        replace: str = None,
        symbol: str = None,
        new_code: str = None,
        edits: list = None,
        start_line: int = None,
        end_line: int = None,
        occurrence: int = None,
    ) -> Dict[str, Any]:
        """
        Apply edit(s) to a file. Five modes:

        1. Search-replace mode (search + replace):
           Single find-replace. search must be unique in the file unless occurrence is set.

        2. Search-replace with occurrence (search + replace + occurrence):
           Replace the Nth occurrence (1-indexed) when search string appears multiple times.

        3. Symbol mode (symbol + new_code):
           Replace entire function/method/class body via index lookup.

        4. Line-range mode (start_line + end_line + new_code):
           Replace lines start_line..end_line (1-indexed, inclusive) with new_code.

        5. Batch mode (edits):
           List of {"search": str, "replace": str} pairs applied sequentially.
           All searches must be unique and non-overlapping.

        Returns compact response: full diff for small changes, summary for large ones.
        """
        # Validate parameters
        has_search = search is not None and replace is not None
        has_symbol = symbol is not None and new_code is not None and start_line is None and end_line is None
        has_batch = edits is not None and len(edits) > 0
        has_line_range = start_line is not None and end_line is not None and new_code is not None and symbol is None

        mode_count = sum([has_search, has_symbol, has_batch, has_line_range])
        if mode_count == 0:
            return {
                "status": "error",
                "message": "Provide (search + replace), (symbol + new_code), (start_line + end_line + new_code), or (edits: [{search, replace}, ...])"
            }
        if mode_count > 1:
            return {
                "status": "error",
                "message": "Use only one mode at a time"
            }

        # Resolve and read file
        try:
            full_path = self._resolve_path(file_path)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if not os.path.exists(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            old_content = self._read_file(full_path)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Apply the edit
        if has_batch:
            result = self._apply_batch(old_content, edits, file_path)
        elif has_search:
            result = self._apply_search_replace(old_content, search, replace, file_path, occurrence)
        elif has_line_range:
            result = self._apply_line_range_replace(old_content, start_line, end_line, new_code, file_path)
        else:
            result = self._apply_symbol_replace(old_content, file_path, symbol, new_code)

        if result["status"] == "error":
            return result

        new_content = result["new_content"]

        if old_content == new_content:
            return {"status": "no_change", "file_path": file_path}

        # Write file
        try:
            self._write_file(full_path, new_content)
        except Exception as e:
            return {"status": "error", "message": f"Write failed: {e}"}

        # Build response (smart diff/summary)
        response = self._build_response(old_content, new_content, file_path)

        # Syntax check after write (PHP, JS/TS, Python, Go, Rust)
        syntax_err = self._check_syntax(full_path)
        if syntax_err:
            # Rollback the file
            self._write_file(full_path, old_content)
            return {
                "status": "error",
                "message": f"Syntax error — edit rolled back: {syntax_err}",
                "file_path": file_path,
            }

        # Instant index refresh for the edited file
        self._refresh_file_index(file_path, full_path)

        # Auto anti-pattern check for PHP and Blade files
        if file_path.endswith(".php"):
            try:
                from .advanced_analysis_service import AdvancedAnalysisService
                ap_result = AdvancedAnalysisService(self.ctx).auto_anti_pattern_check(file_path)
                if ap_result.get("status") == "issues_found":
                    response["anti_pattern_warnings"] = {
                        "errors": ap_result.get("errors", []),
                        "warnings": ap_result.get("warnings", []),
                        "info_count": ap_result.get("info_count", 0),
                    }
            except Exception as e:
                logger.debug("Anti-pattern check failed (non-fatal): %s", e)

        return response

    # ─── Edit Strategies ─────────────────────────────────────────

    def _apply_search_replace(
        self, content: str, search: str, replace: str, file_path: str, occurrence: int = None
    ) -> Dict[str, Any]:
        """Single search-replace. Without occurrence, search must be unique. With occurrence, replaces nth match."""
        count = content.count(search)

        if count == 0:
            preview = search[:100].replace('\n', '\\n')
            return {
                "status": "error",
                "message": f"Search string not found in {file_path}",
                "search_preview": preview,
                "file_lines": len(content.splitlines()),
            }

        if occurrence is not None:
            # Replace specific occurrence
            if occurrence < 1 or occurrence > count:
                return {
                    "status": "error",
                    "message": f"Occurrence {occurrence} out of range (found {count} matches)",
                }
            # Find nth occurrence and replace it
            pos = 0
            for i in range(occurrence):
                pos = content.index(search, pos)
                if i < occurrence - 1:
                    pos += len(search)
            new_content = content[:pos] + replace + content[pos + len(search):]
            return {"status": "ok", "new_content": new_content}

        if count > 1:
            # Find line numbers of all occurrences to help make unique
            lines = content.splitlines()
            occurrence_lines = []
            search_first_line = search.splitlines()[0] if search else ""
            for i, line in enumerate(lines, 1):
                if search_first_line in line:
                    occurrence_lines.append(i)
            return {
                "status": "error",
                "message": f"Found {count} times — must be unique. Add surrounding context or use occurrence=N.",
                "occurrence_lines": occurrence_lines[:10],
                "hint": f"Use occurrence=1..{count} to target a specific match",
            }

        return {"status": "ok", "new_content": content.replace(search, replace, 1)}

    def _apply_batch(
        self, content: str, edits: list, file_path: str
    ) -> Dict[str, Any]:
        """
        Apply multiple search-replace pairs to original content.
        Each edit is {"search": str, "replace": str}.
        Pre-computes all match positions in the ORIGINAL content, then applies
        replacements from last-to-first so earlier replacements don't shift
        later positions (avoids cascading replacement bugs).
        """
        # Phase 1: Validate all searches exist exactly once in original content
        matches = []  # (position, search_len, replace_str)
        for i, edit in enumerate(edits):
            s = edit.get("search")
            r = edit.get("replace")
            if s is None or r is None:
                return {
                    "status": "error",
                    "message": f"Edit #{i+1}: both 'search' and 'replace' required",
                }
            count = content.count(s)
            if count == 0:
                preview = s[:80].replace('\n', '\\n')
                return {
                    "status": "error",
                    "message": f"Edit #{i+1}: search not found",
                    "search_preview": preview,
                }
            if count > 1:
                return {
                    "status": "error",
                    "message": f"Edit #{i+1}: found {count} times — must be unique",
                }
            pos = content.index(s)
            matches.append((pos, len(s), r))

        # Phase 2: Check for overlapping matches
        matches_sorted = sorted(matches, key=lambda m: m[0])
        for i in range(len(matches_sorted) - 1):
            pos_a, len_a, _ = matches_sorted[i]
            pos_b, _, _ = matches_sorted[i + 1]
            if pos_a + len_a > pos_b:
                return {
                    "status": "error",
                    "message": f"Overlapping edits detected at positions {pos_a} and {pos_b}",
                }

        # Phase 3: Apply replacements from end to beginning (reverse position order)
        # so earlier replacements don't shift later positions
        result = content
        for pos, search_len, replace_str in sorted(matches, key=lambda m: m[0], reverse=True):
            result = result[:pos] + replace_str + result[pos + search_len:]

        return {"status": "ok", "new_content": result}

    def _apply_symbol_replace(
        self, content: str, file_path: str, symbol: str, new_code: str
    ) -> Dict[str, Any]:
        """Replace a symbol's entire body with new code."""
        info = self._find_symbol_info(file_path, symbol)
        if info["status"] == "error":
            return info

        lines = content.splitlines(keepends=True)
        start_idx = info["start_line"] - 1
        end_idx = info["end_line"]  # inclusive → slice up to here

        if new_code and not new_code.endswith('\n'):
            new_code += '\n'

        new_lines = lines[:start_idx] + [new_code] + lines[end_idx:]
        return {"status": "ok", "new_content": "".join(new_lines)}

    def _apply_line_range_replace(
        self, content: str, start_line: int, end_line: int, new_code: str, file_path: str
    ) -> Dict[str, Any]:
        """Replace lines start_line..end_line (1-indexed, inclusive) with new_code."""
        lines = content.splitlines(keepends=True)
        total = len(lines)

        if start_line < 1 or end_line < start_line or start_line > total:
            return {
                "status": "error",
                "message": f"Invalid line range {start_line}-{end_line} (file has {total} lines)",
            }

        # Clamp end_line to file length
        end_line = min(end_line, total)

        if new_code and not new_code.endswith('\n'):
            new_code += '\n'

        new_lines = lines[:start_line - 1] + [new_code] + lines[end_line:]
        return {"status": "ok", "new_content": "".join(new_lines)}

    # ─── apply_edit_multi (multi-file batch) ────────────────────

    def apply_edit_multi(self, file_edits: list) -> Dict[str, Any]:
        """
        Apply edits to multiple files in a single call.

        Each item in file_edits: {"file_path": str, "edits": [{"search": str, "replace": str}, ...]}

        All files are validated first. If any file fails validation, no changes are made.
        On success, each file is edited and syntax-checked independently.

        Returns:
            Dictionary with per-file results.
        """
        if not file_edits or not isinstance(file_edits, list):
            return {"status": "error", "message": "file_edits must be a non-empty list"}

        # Phase 1: Validate all files exist and all searches are unique
        validated = []
        for i, item in enumerate(file_edits):
            file_path = item.get("file_path")
            edits = item.get("edits", [])

            if not file_path:
                return {"status": "error", "message": f"Item #{i+1}: file_path required"}
            if not edits:
                return {"status": "error", "message": f"Item #{i+1}: edits list required"}

            try:
                full_path = self._resolve_path(file_path)
            except ValueError as e:
                return {"status": "error", "message": f"Item #{i+1}: {e}"}

            if not os.path.exists(full_path):
                return {"status": "error", "message": f"Item #{i+1}: file not found: {file_path}"}

            try:
                content = self._read_file(full_path)
            except ValueError as e:
                return {"status": "error", "message": f"Item #{i+1}: {e}"}

            # Validate each edit in the file
            for j, edit in enumerate(edits):
                s = edit.get("search")
                r = edit.get("replace")
                if s is None or r is None:
                    return {"status": "error", "message": f"Item #{i+1}, edit #{j+1}: search and replace required"}
                count = content.count(s)
                if count == 0:
                    return {"status": "error", "message": f"Item #{i+1}, edit #{j+1}: search not found in {file_path}"}
                if count > 1:
                    return {"status": "error", "message": f"Item #{i+1}, edit #{j+1}: found {count} times in {file_path} — must be unique"}

            validated.append({
                "file_path": file_path,
                "full_path": full_path,
                "old_content": content,
                "edits": edits,
            })

        # Phase 2a: Create backups of all files (for atomic rollback)
        backups: Dict[str, str] = {}
        for item in validated:
            backups[item["full_path"]] = item["old_content"]

        # Phase 2b: Apply all edits with atomic rollback on failure
        results = []
        applied_files: List[str] = []  # Track successfully written files for rollback

        def _rollback_all():
            """Restore ALL previously written files from backups."""
            for fp in applied_files:
                try:
                    self._write_file(fp, backups[fp])
                except Exception as rb_err:
                    logger.error("Rollback failed for %s: %s", fp, rb_err)

        for item in validated:
            new_content = item["old_content"]
            for edit in item["edits"]:
                new_content = new_content.replace(edit["search"], edit["replace"], 1)

            if new_content == item["old_content"]:
                results.append({"file_path": item["file_path"], "status": "no_change"})
                continue

            # Write
            try:
                self._write_file(item["full_path"], new_content)
            except Exception as e:
                _rollback_all()
                return {
                    "status": "error",
                    "message": f"Write failed for {item['file_path']}: {e} — all files rolled back",
                }

            applied_files.append(item["full_path"])

            # Syntax check (PHP, JS/TS, Python, Go, Rust)
            syntax_err = self._check_syntax(item["full_path"])
            if syntax_err:
                _rollback_all()
                return {
                    "status": "error",
                    "message": f"Syntax error in {item['file_path']}: {syntax_err} — all files rolled back",
                }

            # Build compact response
            old_lines = len(item["old_content"].splitlines())
            new_lines = len(new_content.splitlines())
            results.append({
                "file_path": item["file_path"],
                "status": "success",
                "edits_applied": len(item["edits"]),
                "lines_before": old_lines,
                "lines_after": new_lines,
            })

            # Instant index refresh for the edited file
            self._refresh_file_index(item["file_path"], item["full_path"])

        success_count = sum(1 for r in results if r["status"] == "success")
        return {
            "status": "success" if success_count == len(results) else "partial",
            "files_total": len(results),
            "files_success": success_count,
            "results": results,
        }

    # ─── get_edit_region ─────────────────────────────────────────

    def get_edit_region(
        self,
        file_path: str,
        symbol: str = None,
        start_line: int = None,
        end_line: int = None,
        context_lines: int = 5,
    ) -> Dict[str, Any]:
        """
        Get a region of a file for editing context.

        Two modes:
        1. Symbol mode: provide `symbol` — returns the symbol's code + surrounding context
        2. Line mode: provide `start_line` and `end_line` — returns that range + context

        Returns the region with line numbers for crafting precise edits.
        """
        try:
            full_path = self._resolve_path(file_path)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if not os.path.exists(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            content = self._read_file(full_path)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        lines = content.splitlines()
        total_lines = len(lines)

        # Determine target range
        if symbol:
            info = self._find_symbol_info(file_path, symbol)
            if info["status"] == "error":
                return info
            target_start = info["start_line"]
            target_end = info["end_line"]
        elif start_line is not None and end_line is not None:
            target_start = max(1, start_line)
            target_end = min(total_lines, end_line)
        else:
            return {
                "status": "error",
                "message": "Provide 'symbol' or both 'start_line' and 'end_line'"
            }

        # Add context
        region_start = max(1, target_start - context_lines)
        region_end = min(total_lines, target_end + context_lines)

        # Build numbered lines
        region_lines = []
        for i in range(region_start - 1, region_end):
            region_lines.append(f"{i + 1:4d} | {lines[i]}")

        return {
            "status": "success",
            "file_path": file_path,
            "total_lines": total_lines,
            "region_start": region_start,
            "region_end": region_end,
            "target_start": target_start,
            "target_end": target_end,
            "content": "\n".join(region_lines),
        }
