"""
SQLite-backed index manager coordinating builder and store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sqlite_index_builder import SQLiteIndexBuilder
from .sqlite_store import SQLiteIndexStore, SQLiteSchemaMismatchError
from ..constants import INDEX_FILE_DB, INDEX_FILE, INDEX_FILE_SHALLOW, SETTINGS_DIR

logger = logging.getLogger(__name__)


class SQLiteIndexManager:
    """Manage lifecycle of SQLite-backed deep index."""

    def __init__(self) -> None:
        self.project_path: Optional[str] = None
        self.index_builder: Optional[SQLiteIndexBuilder] = None
        self.store: Optional[SQLiteIndexStore] = None
        self.temp_dir: Optional[str] = None
        self.index_path: Optional[str] = None
        self.shallow_index_path: Optional[str] = None
        self._shallow_file_list: Optional[List[str]] = None
        self._is_loaded = False
        self._lock = threading.RLock()
        logger.info("Initialized SQLite Index Manager")

    def set_project_path(self, project_path: str, additional_excludes: Optional[List[str]] = None) -> bool:
        """Configure project path and underlying storage location.

        Args:
            project_path: Path to the project directory to index
            additional_excludes: Optional list of additional directory/file
                patterns to exclude from indexing (e.g., ['vendor', 'custom_deps'])

        Returns:
            True if configuration succeeded, False otherwise
        """
        with self._lock:
            if not project_path or not isinstance(project_path, str):
                logger.error("Invalid project path: %s", project_path)
                return False

            project_path = project_path.strip()
            if not project_path or not os.path.isdir(project_path):
                logger.error("Project path does not exist: %s", project_path)
                return False

            self.project_path = project_path
            project_hash = _hash_project_path(project_path)
            self.temp_dir = os.path.join(tempfile.gettempdir(), SETTINGS_DIR, project_hash)
            os.makedirs(self.temp_dir, exist_ok=True)

            self.index_path = os.path.join(self.temp_dir, INDEX_FILE_DB)
            legacy_path = os.path.join(self.temp_dir, INDEX_FILE)
            if os.path.exists(legacy_path):
                try:
                    os.remove(legacy_path)
                    logger.info("Removed legacy JSON index at %s", legacy_path)
                except OSError as exc:  # pragma: no cover - best effort
                    logger.warning("Failed to remove legacy index %s: %s", legacy_path, exc)

            self.shallow_index_path = os.path.join(self.temp_dir, INDEX_FILE_SHALLOW)
            self.store = SQLiteIndexStore(self.index_path)
            self.index_builder = SQLiteIndexBuilder(project_path, self.store, additional_excludes)
            self._is_loaded = False
            logger.info("SQLite index storage: %s", self.index_path)
            if additional_excludes:
                logger.info("Additional excludes: %s", additional_excludes)
            return True

    def build_index(self, force_rebuild: bool = False) -> bool:
        """Build or rebuild the SQLite index."""
        with self._lock:
            if not self.index_builder:
                logger.error("Index builder not initialized")
                return False
            try:
                stats = self.index_builder.build_index()
                logger.info(
                    "SQLite index build complete: %s files, %s symbols",
                    stats.get("files"),
                    stats.get("symbols"),
                )
                self._is_loaded = True
                return True
            except SQLiteSchemaMismatchError:
                logger.warning("Schema mismatch detected; recreating database")
                self.store.clear()  # type: ignore[union-attr]
                stats = self.index_builder.build_index()
                logger.info(
                    "SQLite index rebuild after schema reset: %s files, %s symbols",
                    stats.get("files"),
                    stats.get("symbols"),
                )
                self._is_loaded = True
                return True
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Failed to build SQLite index: %s", exc)
                self._is_loaded = False
                return False

    def load_index(self) -> bool:
        """Validate that an index database exists and schema is current."""
        with self._lock:
            if not self.store:
                logger.error("Index store not initialized")
                return False
            try:
                self.store.initialize_schema()
                with self.store.connect() as conn:
                    metadata = self.store.get_metadata(conn, "index_metadata")
            except SQLiteSchemaMismatchError:
                logger.info("Schema mismatch on load; forcing rebuild on next build_index()")
                self._is_loaded = False
                return False
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to load SQLite index: %s", exc)
                self._is_loaded = False
                return False
            self._is_loaded = metadata is not None
            return self._is_loaded

    def refresh_index(self) -> bool:
        """Force rebuild of the SQLite index."""
        with self._lock:
            logger.info("Refreshing SQLite deep index...")
            if self.build_index(force_rebuild=True):
                return self.load_index()
            return False

    def build_shallow_index(self) -> bool:
        """Build the shallow index file list using existing builder helper."""
        with self._lock:
            if not self.index_builder or not self.project_path or not self.shallow_index_path:
                logger.error("Index builder not initialized for shallow index")
                return False
            try:
                file_list = self.index_builder.build_shallow_file_list()
                with open(self.shallow_index_path, "w", encoding="utf-8") as handle:
                    json.dump(file_list, handle, ensure_ascii=False)
                self._shallow_file_list = file_list
                return True
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to build shallow index: %s", exc)
                return False

    def load_shallow_index(self) -> bool:
        """Load shallow index from disk."""
        with self._lock:
            if not self.shallow_index_path or not os.path.exists(self.shallow_index_path):
                return False
            try:
                with open(self.shallow_index_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, list):
                    self._shallow_file_list = [_normalize_path(p) for p in data if isinstance(p, str)]
                    return True
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to load shallow index: %s", exc)
            return False

    def find_files(self, pattern: str = "*") -> List[str]:
        """Find files from the shallow index using glob semantics."""
        with self._lock:
            if not isinstance(pattern, str):
                logger.error("Pattern must be a string, got %s", type(pattern))
                return []
            pattern = pattern.strip() or "*"
            norm_pattern = pattern.replace("\\\\", "/").replace("\\", "/")
            regex = _compile_glob_regex(norm_pattern)

            if self._shallow_file_list is None:
                if not self.load_shallow_index():
                    if self.build_shallow_index():
                        self.load_shallow_index()

            files = list(self._shallow_file_list or [])
            if norm_pattern == "*":
                return files
            return [f for f in files if regex.match(f)]

    def get_file_summary(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Return summary information for a file from SQLite storage."""
        with self._lock:
            if not isinstance(file_path, str):
                logger.error("File path must be a string, got %s", type(file_path))
                return None
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return None

            normalized = _normalize_path(file_path)
            with self.store.connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, language, line_count, imports, exports, docstring
                    FROM files WHERE path = ?
                    """,
                    (normalized,),
                ).fetchone()

                if not row:
                    logger.warning("File not found in index: %s", normalized)
                    return None

                symbol_rows = conn.execute(
                    """
                    SELECT type, line, end_line, signature, docstring, called_by, short_name
                    FROM symbols
                    WHERE file_id = ?
                    ORDER BY line ASC
                    """,
                    (row["id"],),
                ).fetchall()

            imports = _safe_json_loads(row["imports"])
            exports = _safe_json_loads(row["exports"])

            categorized = _categorize_symbols(symbol_rows)

            return {
                "file_path": normalized,
                "language": row["language"],
                "line_count": row["line_count"],
                "symbol_count": len(symbol_rows),
                "functions": categorized["functions"],
                "classes": categorized["classes"],
                "methods": categorized["methods"],
                "imports": imports,
                "exports": exports,
                "docstring": row["docstring"],
            }

    # ── Index-backed relationship queries ─────────────────────────

    def find_callers(
        self,
        called_short_name: Optional[str] = None,
        called_file_path: Optional[str] = None,
        called_symbol_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return caller symbol rows for a given callee.

        The callee can be specified by symbol_id (most precise), by
        short_name scoped to a file, or by short_name alone. When
        ``owner`` is supplied, matches are narrowed to symbols whose
        short_name is exactly ``owner.called_short_name`` (or ends in
        ``.owner.called_short_name``), disambiguating identically named
        methods defined on multiple classes.
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []

            with self.store.connect() as conn:
                callee_ids = self._resolve_callee_ids(
                    conn,
                    called_short_name=called_short_name,
                    called_file_path=called_file_path,
                    called_symbol_id=called_symbol_id,
                    owner=owner,
                )
                if not callee_ids:
                    return []

                placeholders = ",".join("?" * len(callee_ids))
                rows = conn.execute(
                    f"""
                    SELECT
                        r.caller_symbol_id AS caller_symbol_id,
                        r.called_symbol_id AS called_symbol_id,
                        cs.short_name AS caller_short_name,
                        cs.line AS caller_line,
                        cs.type AS caller_type,
                        cf.path AS caller_file,
                        ds.short_name AS called_short_name,
                        df.path AS called_file
                    FROM refs r
                    JOIN symbols cs ON cs.symbol_id = r.caller_symbol_id
                    JOIN files cf ON cf.id = r.caller_file_id
                    JOIN symbols ds ON ds.symbol_id = r.called_symbol_id
                    JOIN files df ON df.id = r.called_file_id
                    WHERE r.called_symbol_id IN ({placeholders})
                    """,
                    list(callee_ids),
                ).fetchall()

            return [dict(row) for row in rows]

    def find_callers_for_file(self, called_file_path: str) -> List[Dict[str, Any]]:
        """Return caller rows for every callee defined in the given file."""
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []

            normalized = _normalize_path(called_file_path)
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        r.caller_symbol_id AS caller_symbol_id,
                        r.called_symbol_id AS called_symbol_id,
                        cs.short_name AS caller_short_name,
                        cs.line AS caller_line,
                        cs.type AS caller_type,
                        cf.path AS caller_file,
                        ds.short_name AS called_short_name,
                        df.path AS called_file
                    FROM refs r
                    JOIN symbols cs ON cs.symbol_id = r.caller_symbol_id
                    JOIN files cf ON cf.id = r.caller_file_id
                    JOIN symbols ds ON ds.symbol_id = r.called_symbol_id
                    JOIN files df ON df.id = r.called_file_id
                    WHERE df.path = ?
                    """,
                    (normalized,),
                ).fetchall()
            return [dict(row) for row in rows]

    def find_symbols_by_short_name(self, short_name: str) -> List[Dict[str, Any]]:
        """Return all symbol rows (across all files) with the given short_name."""
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        s.symbol_id AS symbol_id,
                        s.short_name AS short_name,
                        s.type AS type,
                        s.line AS line,
                        s.end_line AS end_line,
                        s.signature AS signature,
                        f.path AS file
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.short_name = ?
                    """,
                    (short_name,),
                ).fetchall()
            return [dict(row) for row in rows]

    def list_all_classes(self) -> List[Dict[str, Any]]:
        """Return every class symbol across the project (single SQL pass)."""
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        s.short_name AS name,
                        s.line AS line,
                        s.end_line AS end_line,
                        s.signature AS signature,
                        f.path AS file
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.type = 'class'
                    """
                ).fetchall()
            return [dict(row) for row in rows]

    def list_indexed_files(self) -> List[str]:
        """Return every file path known to the deep index."""
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []
            with self.store.connect() as conn:
                rows = conn.execute("SELECT path FROM files").fetchall()
            return [row["path"] for row in rows]

    def list_file_symbol_counts(self) -> Dict[str, int]:
        """Return ``{file_path: symbol_count}`` for hotspot ranking.

        Counts only callable/structural symbols (class/function/method)
        so a file with many locals but no real API still registers as
        low-density. Single SQL pass used by snapshot hotspot detection.
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return {}
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT f.path AS path, COUNT(*) AS cnt
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.type IN ('class', 'function', 'method', 'interface')
                    GROUP BY f.path
                    """
                ).fetchall()
            return {row["path"]: int(row["cnt"]) for row in rows}

    def list_file_imports(self) -> Dict[str, List[str]]:
        """Return ``{file_path: [imports, ...]}`` for every indexed file.

        Single SQL pass used by the project-snapshot cross-reference
        graph. Replaces a per-file ``get_file_summary`` loop that cost
        ~26 ms/file on large repos.
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return {}
            with self.store.connect() as conn:
                rows = conn.execute(
                    "SELECT path, imports FROM files WHERE imports IS NOT NULL AND imports != '[]'"
                ).fetchall()
            out: Dict[str, List[str]] = {}
            for row in rows:
                try:
                    parsed = json.loads(row["imports"]) if row["imports"] else []
                except (ValueError, TypeError):
                    parsed = []
                if parsed:
                    out[row["path"]] = parsed
            return out

    def list_class_method_counts(self) -> Dict[Tuple[str, str], int]:
        """Return ``{(file_path, class_short_name): method_count}``.

        Single SQL pass that replaces the per-file ``get_file_summary``
        loop in the project-snapshot path. Essential on large repos
        (9k+ files): the old loop issued one query per file; this one
        covers the entire index in a single ``GROUP BY``.
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return {}
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT f.path AS file,
                           SUBSTR(s.short_name, 1, INSTR(s.short_name, '.') - 1) AS owner,
                           COUNT(*) AS cnt
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.short_name LIKE '%.%'
                      AND s.type IN ('method', 'function')
                    GROUP BY f.path, owner
                    """
                ).fetchall()
            return {(row["file"], row["owner"]): int(row["cnt"]) for row in rows}

    def _resolve_callee_ids(
        self,
        conn,
        called_short_name: Optional[str] = None,
        called_file_path: Optional[str] = None,
        called_symbol_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> List[str]:
        if called_symbol_id:
            row = conn.execute(
                "SELECT 1 FROM symbols WHERE symbol_id = ?", (called_symbol_id,)
            ).fetchone()
            return [called_symbol_id] if row else []

        if not called_short_name:
            return []

        # When owner is provided, the callee is namespaced by the class
        # or module name. Match either the exact combined form or a
        # qualified suffix ending in that form.
        if owner:
            qualified = f"{owner}.{called_short_name}"
            suffix_pattern = f"%.{qualified}"
            if called_file_path:
                normalized = _normalize_path(called_file_path)
                rows = conn.execute(
                    """
                    SELECT s.symbol_id AS symbol_id
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE f.path = ? AND (
                        s.short_name = ?
                        OR s.short_name LIKE ?
                    )
                    """,
                    (normalized, qualified, suffix_pattern),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol_id FROM symbols
                    WHERE short_name = ?
                       OR short_name LIKE ?
                    """,
                    (qualified, suffix_pattern),
                ).fetchall()
            return [row["symbol_id"] for row in rows]

        if called_file_path:
            normalized = _normalize_path(called_file_path)
            rows = conn.execute(
                """
                SELECT s.symbol_id AS symbol_id
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE f.path = ? AND (
                    s.short_name = ?
                    OR s.short_name LIKE ?
                )
                """,
                (normalized, called_short_name, f"%.{called_short_name}"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT symbol_id FROM symbols
                WHERE short_name = ?
                   OR short_name LIKE ?
                """,
                (called_short_name, f"%.{called_short_name}"),
            ).fetchall()
        return [row["symbol_id"] for row in rows]

    def find_cochanged_files(self, file_path: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Return files that historically changed together with ``file_path``.

        Reads from the ``cochanges`` table populated at index build time
        from a bounded ``git log`` scan. Returns an empty list when no
        git history is available.
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []

            normalized = _normalize_path(file_path)
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        CASE WHEN file_a = ? THEN file_b ELSE file_a END AS peer,
                        count,
                        last_seen
                    FROM cochanges
                    WHERE file_a = ? OR file_b = ?
                    ORDER BY count DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (normalized, normalized, normalized, int(limit)),
                ).fetchall()
            return [dict(row) for row in rows]

    def search_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Run a BM25 query over indexed symbols.

        When the optional embedding index is populated, BM25 and cosine
        rankings are blended via Reciprocal Rank Fusion to leverage both
        lexical and semantic signals. Top-K results are then rescored
        with owner/type/decorator-aware signals so class methods and
        framework-tagged APIs rank above incidental helpers when the
        query describes a capability (see ``_rerank_by_intent``).
        """
        with self._lock:
            if not self.store or not self._is_loaded:
                if not self.load_index():
                    return []

            from .search_index import query_bm25, tokenize
            from .embedding_index import query_embeddings

            with self.store.connect() as conn:
                # Over-fetch so the intent reranker can surface a method
                # whose BM25 rank sits below the owner-token cluster.
                # Floor raised to 100 so queries inside large classes
                # (50+ methods) do not lose the correct method to early
                # truncation before ``_rerank_by_intent`` runs.
                overfetch = max(limit * 5, 100)
                bm25 = query_bm25(conn, query, limit=overfetch)
                emb = query_embeddings(conn, query, limit=overfetch)
                ranked = _rrf_blend(bm25, emb, limit=overfetch) if emb else bm25[:overfetch]
                if not ranked:
                    return []
                ids = [sid for sid, _ in ranked]
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"""
                    SELECT
                        s.symbol_id AS symbol_id,
                        s.short_name AS short_name,
                        s.type AS type,
                        s.line AS line,
                        s.end_line AS end_line,
                        s.signature AS signature,
                        s.docstring AS docstring,
                        f.path AS file
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.symbol_id IN ({placeholders})
                    """,
                    ids,
                ).fetchall()
                by_id = {row["symbol_id"]: dict(row) for row in rows}

            results: List[Dict[str, Any]] = []
            for sid, score in ranked:
                row = by_id.get(sid)
                if row:
                    row["score"] = float(score)
                    results.append(row)

            query_tokens = set(tokenize(query))
            results = _rerank_by_intent(results, query_tokens)
            for row in results:
                row["score"] = round(row["score"], 4)
            return results[:limit]

    def get_index_stats(self) -> Dict[str, Any]:
        """Return basic statistics for the current index."""
        with self._lock:
            if not self.store:
                return {"status": "not_loaded"}
            try:
                with self.store.connect() as conn:
                    metadata = self.store.get_metadata(conn, "index_metadata")
            except SQLiteSchemaMismatchError:
                return {"status": "not_loaded"}
            if not metadata:
                return {"status": "not_loaded"}
            return {
                "status": "loaded" if self._is_loaded else "not_loaded",
                "indexed_files": metadata.get("indexed_files", 0),
                "total_symbols": metadata.get("total_symbols", 0),
                "symbol_types": metadata.get("symbol_types", {}),
                "languages": metadata.get("languages", []),
                "project_path": metadata.get("project_path"),
                "timestamp": metadata.get("timestamp"),
            }

    def cleanup(self) -> None:
        """Reset internal state."""
        with self._lock:
            self.project_path = None
            self.index_builder = None
            self.store = None
            self.temp_dir = None
            self.index_path = None
            self._shallow_file_list = None
            self._is_loaded = False


def _hash_project_path(project_path: str) -> str:
    import hashlib

    return hashlib.md5(project_path.encode()).hexdigest()[:12]


def _compile_glob_regex(pattern: str):
    i = 0
    out = []
    special = ".^$+{}[]|()"
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c in special:
            out.append("\\" + c)
        else:
            out.append(c)
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _normalize_path(path: str) -> str:
    result = path.replace("\\\\", "/").replace("\\", "/")
    if result.startswith("./"):
        result = result[2:]
    return result


def _safe_json_loads(value: Any) -> List[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _categorize_symbols(symbol_rows) -> Dict[str, List[Dict[str, Any]]]:
    functions: List[Dict[str, Any]] = []
    classes: List[Dict[str, Any]] = []
    methods: List[Dict[str, Any]] = []

    for row in symbol_rows:
        short_name = row["short_name"] or ""

        # Skip symbols with invalid names (broken parsing artifacts)
        # Valid identifiers: start with letter/$/_, contain only word chars/$
        # Also allow Class.method format for methods
        if short_name and not re.match(r'^[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*$', short_name):
            continue

        symbol_type = row["type"]
        called_by = _safe_json_loads(row["called_by"])
        info = {
            "name": short_name,
            "called_by": called_by,
            "line": row["line"],
            "end_line": row["end_line"],
            "signature": row["signature"],
            "docstring": row["docstring"],
        }

        signature = row["signature"] or ""
        if signature.startswith("def ") and "::" in signature:
            methods.append(info)
        elif signature.startswith("def "):
            functions.append(info)
        elif signature.startswith("class ") or symbol_type == "class":
            classes.append(info)
        else:
            if symbol_type == "method":
                methods.append(info)
            elif symbol_type == "class":
                classes.append(info)
            else:
                functions.append(info)

    functions.sort(key=lambda item: item.get("line") or 0)
    classes.sort(key=lambda item: item.get("line") or 0)
    methods.sort(key=lambda item: item.get("line") or 0)

    return {
        "functions": functions,
        "classes": classes,
        "methods": methods,
    }


def _rrf_blend(
    bm25: List[tuple],
    embeddings: List[tuple],
    limit: int,
    k: int = 60,
) -> List[tuple]:
    """Reciprocal Rank Fusion of two ``(symbol_id, score)`` rankings."""
    fused: Dict[str, float] = {}
    for rank, (sid, _) in enumerate(bm25):
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (k + rank + 1)
    for rank, (sid, _) in enumerate(embeddings):
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (k + rank + 1)
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    return ordered[:limit]


# Type weights used by the intent reranker. Methods on classes are the
# canonical API surface; loose functions rank just below; classes and
# interfaces sit below their methods because a capability query almost
# always points at the *behaviour*, not the type-shape.
_TYPE_WEIGHTS: Dict[str, float] = {
    "method": 1.15,
    "function": 1.05,
    "class": 0.90,
    "interface": 0.75,
    "trait": 0.75,
    "enum": 0.85,
}

# Framework/decorator tokens whose presence in a signature correlates
# with "real" service-layer APIs in modern TS/Python/PHP codebases.
# Mild boost, not a gate — unlabeled code stays fully reachable.
_FRAMEWORK_MARKERS = (
    "@injectable", "@controller", "@service", "@component",
    "@route", "@get", "@post", "@put", "@patch", "@delete",
    "@restcontroller", "@app.route", "@router.", "#[route",
)


def _rerank_by_intent(results: List[Dict[str, Any]], query_tokens: set) -> List[Dict[str, Any]]:
    """Rescore BM25+embedding candidates with owner/type/decorator signals.

    Solves two concrete failures seen on real NestJS repos:
    1. Owner-token flooding — every method of ``ProviderTokenVerifierService``
       outranking ``AuthService.authenticate`` because the file path and
       class name both carry the query tokens. Fixed by a method-name-
       match bonus and an owner-only-match penalty.
    2. Type-shape noise — ``interface`` and ``enum`` entries outranking
       callable methods. Fixed by a per-type multiplier.

    FP guard: the penalty fires only when method/signature/docstring all
    miss the query, so a legitimate ``UserService``-style owner query
    still surfaces the matching class entry. FN guard: every adjustment
    is multiplicative on the existing BM25+embedding score — nothing is
    dropped, only reordered.
    """
    if not results or not query_tokens:
        return results

    from .search_index import tokenize

    for row in results:
        base = row.get("score", 0.0) or 0.0
        short = row.get("short_name") or ""
        sig = row.get("signature") or ""
        doc = row.get("docstring") or ""
        sym_type = (row.get("type") or "").lower()

        if "." in short:
            owner_part, _, method_part = short.rpartition(".")
        else:
            owner_part, method_part = "", short

        method_tokens = set(tokenize(method_part))
        owner_tokens = set(tokenize(owner_part)) if owner_part else set()
        sig_tokens = set(tokenize(sig))
        doc_tokens = set(tokenize(doc))

        weight = _TYPE_WEIGHTS.get(sym_type, 1.0)

        # Strongest signal: the query names the thing being described.
        if method_tokens & query_tokens:
            weight *= 1.55

        # Owner-only match: query hit the owner/path cluster but nothing
        # in the method body. Dampen so classmates of a good match don't
        # all ride up together.
        content_hits = (method_tokens | sig_tokens | doc_tokens) & query_tokens
        owner_hits = owner_tokens & query_tokens
        if owner_hits and not content_hits:
            weight *= 0.65

        # Mild framework-tag boost: decorators in the captured signature
        # indicate the symbol is a real service/route entry point rather
        # than a helper. Useful on NestJS / FastAPI / Laravel projects.
        sig_lc = sig.lower()
        if any(marker in sig_lc for marker in _FRAMEWORK_MARKERS):
            weight *= 1.10

        row["score"] = base * weight

    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return results
