"""
SQLite-backed index builder leveraging existing strategy pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from typing import Dict, Iterable, List, Optional, Tuple


# Reject short_name values that are clearly code fragments rather than
# identifiers. Strategies that fail to extract a clean name sometimes
# emit truncated source like ``"s() {\n            "``; those poison
# BM25 alternatives and suffix-based call resolution.
#
# FP guard: allow alphanumerics, underscores, dots (``Class.method``),
# dollar (PHP/JS), hyphen, and Unicode letters. Reject anything that
# contains whitespace, brackets, quotes, or newlines.
# FN guard: also reject empty/overly long names (>128 chars) which are
# invariably source spans, not identifiers.
_SHORT_NAME_BAD_CHARS = re.compile(r"[\s\(\)\{\}\[\]\"';,`]")
_SHORT_NAME_MAX_LEN = 128


def _is_clean_short_name(name: Optional[str]) -> bool:
    if not name:
        return False
    if len(name) > _SHORT_NAME_MAX_LEN:
        return False
    return _SHORT_NAME_BAD_CHARS.search(name) is None

from .json_index_builder import JSONIndexBuilder
from .sqlite_store import SQLiteIndexStore
from .models import FileInfo, SymbolInfo
from .cochange import collect_cochanges, write_cochanges
from .search_index import build_search_index
from .embedding_index import build_embedding_index, embeddings_enabled

logger = logging.getLogger(__name__)


class SQLiteIndexBuilder(JSONIndexBuilder):
    """
    Build the deep index directly into SQLite storage.

    Inherits scanning/strategy utilities from JSONIndexBuilder but writes rows
    to the provided SQLiteIndexStore instead of assembling large dictionaries.
    """

    def __init__(
        self,
        project_path: str,
        store: SQLiteIndexStore,
        additional_excludes: Optional[List[str]] = None,
    ):
        super().__init__(project_path, additional_excludes)
        self.store = store

    def build_index(
        self,
        parallel: bool = True,
        max_workers: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Build the SQLite index and return lightweight statistics.

        Args:
            parallel: Whether to parse files in parallel.
            max_workers: Optional override for worker count.

        Returns:
            Dictionary with totals for files, symbols, and languages.
        """
        logger.info("Building SQLite index (parallel=%s)...", parallel)
        start_time = time.time()

        files_to_process = self._get_supported_files()
        total_files = len(files_to_process)
        if total_files == 0:
            logger.warning("No files to process")
            with self.store.connect(for_build=True) as conn:
                self._reset_database(conn)
                self._persist_metadata(conn, 0, 0, [], 0, 0, {})
            return {
                "files": 0,
                "symbols": 0,
                "languages": 0,
            }

        specialized_extensions = set(self.strategy_factory.get_specialized_extensions())

        results_iter: Iterable[Tuple[Dict[str, SymbolInfo], Dict[str, FileInfo], str, bool]]

        executor = None

        if parallel and total_files > 1:
            if max_workers is None:
                max_workers = min(os.cpu_count() or 4, total_files)
            logger.info("Using ThreadPoolExecutor with %s workers", max_workers)
            executor = ThreadPoolExecutor(max_workers=max_workers)
            future_to_file = {
                executor.submit(self._process_file, file_path, specialized_extensions): file_path
                for file_path in files_to_process
            }

            def _iter_results():
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        result = future.result(timeout=30)
                        if result:
                            yield result
                    except FutureTimeoutError:
                        logger.warning("Timeout processing file: %s (skipped)", file_path)
                    except Exception as exc:
                        logger.warning("Error processing file %s: %s (skipped)", file_path, exc)

            results_iter = _iter_results()
        else:
            logger.info("Using sequential processing")

            def _iter_results_sequential():
                for file_path in files_to_process:
                    result = self._process_file(file_path, specialized_extensions)
                    if result:
                        yield result

            results_iter = _iter_results_sequential()

        languages = set()
        specialized_count = 0
        fallback_count = 0
        pending_calls: List[Tuple[str, str]] = []
        total_symbols = 0
        symbol_types: Dict[str, int] = {}
        processed_files = 0

        self.store.initialize_schema()
        with self.store.connect(for_build=True) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            self._reset_database(conn)

            for symbols, file_info_dict, language, is_specialized in results_iter:
                file_path, file_info = next(iter(file_info_dict.items()))
                file_id = self._insert_file(conn, file_path, file_info)
                file_pending = getattr(file_info, "pending_calls", [])
                if file_pending:
                    pending_calls.extend(file_pending)
                symbol_rows = self._prepare_symbol_rows(symbols, file_id)

                if symbol_rows:
                    conn.executemany(
                        """
                        INSERT INTO symbols(
                            symbol_id,
                            file_id,
                            type,
                            line,
                            end_line,
                            signature,
                            docstring,
                            called_by,
                            short_name
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        symbol_rows,
                    )

                languages.add(language)
                processed_files += 1
                total_symbols += len(symbol_rows)

                if is_specialized:
                    specialized_count += 1
                else:
                    fallback_count += 1

                for _, _, symbol_type, _, _, _, _, _, _ in symbol_rows:
                    key = symbol_type or "unknown"
                    symbol_types[key] = symbol_types.get(key, 0) + 1

            self._persist_metadata(
                conn,
                processed_files,
                total_symbols,
                sorted(languages),
                specialized_count,
                fallback_count,
                symbol_types,
            )
            self._resolve_pending_calls_sqlite(conn, pending_calls)

            # Co-change signal: bounded git-log scan, indexed files only.
            try:
                indexed_paths = {
                    row["path"] for row in conn.execute("SELECT path FROM files")
                }
                counts, last_seen = collect_cochanges(
                    self.project_path, indexed_files=indexed_paths
                )
                write_cochanges(conn, counts, last_seen)
                if counts:
                    logger.info("Co-change pairs recorded: %s", len(counts))
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Co-change extraction failed: %s", exc)

            # BM25 retrieval index over symbol short_name + signature + docstring.
            try:
                build_search_index(conn)
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Search index build failed: %s", exc)

            # Optional dense embedding index — opt-in, skipped otherwise.
            if embeddings_enabled():
                try:
                    build_embedding_index(conn)
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("Embedding index build failed: %s", exc)

            try:
                conn.execute("PRAGMA optimize")
            except Exception:  # pragma: no cover - best effort
                pass

        if executor:
            executor.shutdown(wait=True)

        elapsed = time.time() - start_time
        logger.info(
            "SQLite index built: files=%s symbols=%s languages=%s elapsed=%.2fs",
            processed_files,
            total_symbols,
            len(languages),
            elapsed,
        )

        return {
            "files": processed_files,
            "symbols": total_symbols,
            "languages": len(languages),
        }

    def reindex_file(self, rel_path: str, full_path: str) -> bool:
        """
        Re-index a single file in the SQLite store.

        Deletes existing data for the file and re-parses + inserts fresh data.
        This keeps the index consistent after an in-place file edit.

        Args:
            rel_path: Relative path of the file (as stored in the index).
            full_path: Absolute path to the file on disk.

        Returns:
            True if re-indexing succeeded, False otherwise.
        """
        try:
            specialized_extensions = set(self.strategy_factory.get_specialized_extensions())
            result = self._process_file(full_path, specialized_extensions)

            with self.store.connect(for_build=True) as conn:
                # Delete existing file entry (CASCADE removes its symbols)
                conn.execute("DELETE FROM files WHERE path = ?", (rel_path,))

                if not result:
                    logger.debug("reindex_file: _process_file returned None for %s", rel_path)
                    return False

                symbols, file_info_dict, language, is_specialized = result
                # Normalize key to relative path — _process_file returns absolute path key
                for abs_key in list(file_info_dict.keys()):
                    if abs_key != rel_path:
                        file_info_dict[rel_path] = file_info_dict.pop(abs_key)
                        break
                file_path_key, file_info = next(iter(file_info_dict.items()))
                file_id = self._insert_file(conn, file_path_key, file_info)
                symbol_rows = self._prepare_symbol_rows(symbols, file_id)

                if symbol_rows:
                    conn.executemany(
                        """
                        INSERT INTO symbols(
                            symbol_id,
                            file_id,
                            type,
                            line,
                            end_line,
                            signature,
                            docstring,
                            called_by,
                            short_name
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        symbol_rows,
                    )

                file_pending = list(getattr(file_info, "pending_calls", []) or [])
                if file_pending:
                    self._resolve_pending_calls_sqlite(conn, file_pending)

            logger.debug(
                "reindex_file: updated %s (%d symbols)",
                rel_path,
                len(symbol_rows) if result else 0,
            )
            return True
        except Exception as exc:
            logger.warning("reindex_file failed for %s: %s", rel_path, exc)
            return False

    # Internal helpers -------------------------------------------------

    def _reset_database(self, conn):
        conn.execute("DELETE FROM refs")
        conn.execute("DELETE FROM embeddings")
        conn.execute("DELETE FROM search_postings")
        conn.execute("DELETE FROM search_docs")
        conn.execute("DELETE FROM search_stats")
        conn.execute("DELETE FROM cochanges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "DELETE FROM metadata WHERE key NOT IN ('schema_version')"
        )

    def _insert_file(self, conn, path: str, file_info: FileInfo) -> int:
        params = (
            path,
            file_info.language,
            file_info.line_count,
            json.dumps(file_info.imports or []),
            json.dumps(file_info.exports or []),
            file_info.package,
            file_info.docstring,
        )
        cur = conn.execute(
            """
            INSERT INTO files(
                path,
                language,
                line_count,
                imports,
                exports,
                package,
                docstring
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        return cur.lastrowid

    def _prepare_symbol_rows(
        self,
        symbols: Dict[str, SymbolInfo],
        file_id: int,
    ) -> List[Tuple[str, int, Optional[str], Optional[int], Optional[int], Optional[str], Optional[str], str, str]]:
        rows: List[Tuple[str, int, Optional[str], Optional[int], Optional[int], Optional[str], Optional[str], str, str]] = []
        for symbol_id, symbol_info in symbols.items():
            short_name = symbol_id.split("::")[-1]
            # Skip symbols whose short_name is a raw code fragment
            # rather than an identifier; they are BM25 noise and would
            # never be resolvable by name anyway.
            if not _is_clean_short_name(short_name):
                continue
            called_by = json.dumps(symbol_info.called_by or [])
            rows.append(
                (
                    symbol_id,
                    file_id,
                    symbol_info.type,
                    symbol_info.line,
                    symbol_info.end_line,
                    symbol_info.signature,
                    symbol_info.docstring,
                    called_by,
                    short_name,
                )
            )
        return rows

    def _persist_metadata(
        self,
        conn,
        file_count: int,
        symbol_count: int,
        languages: List[str],
        specialized_count: int,
        fallback_count: int,
        symbol_types: Dict[str, int],
    ) -> None:
        metadata = {
            "project_path": self.project_path,
            "indexed_files": file_count,
            "index_version": "3.0.0-sqlite",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "languages": languages,
            "total_symbols": symbol_count,
            "specialized_parsers": specialized_count,
            "fallback_files": fallback_count,
            "symbol_types": symbol_types,
        }
        self.store.set_metadata(conn, "project_path", self.project_path)
        self.store.set_metadata(conn, "index_metadata", metadata)

    def _resolve_pending_calls_sqlite(
        self,
        conn,
        pending_calls: List[Tuple[str, str]]
    ) -> None:
        """Resolve cross-file call relationships directly in SQLite storage.

        Produces two outputs:
        - Back-compatible `called_by` JSON column on each `symbols` row.
        - Normalized rows in `refs(caller_symbol_id, called_symbol_id,
          caller_file_id, called_file_id)` used by the query-time engine
          for O(1) cross-file reference lookup.
        """
        if not pending_calls:
            return

        rows = list(conn.execute(
            "SELECT symbol_id, short_name, called_by, file_id FROM symbols"
        ))
        symbol_map = {row["symbol_id"]: row for row in rows}

        short_name_index: Dict[str, List[str]] = defaultdict(list)
        suffix_index: Dict[str, List[str]] = defaultdict(list)
        for row in rows:
            short_name = row["short_name"] or ""
            symbol_id = row["symbol_id"]
            if short_name:
                short_name_index[short_name].append(symbol_id)

            parts = short_name.split(".") if short_name else []
            # Record proper suffixes only (exclude the full name) to match the
            # previous suffix-scan behavior that required a leading dot.
            for i in range(1, len(parts)):
                suffix = ".".join(parts[-i:])
                suffix_index[suffix].append(symbol_id)

        updates: Dict[str, set] = defaultdict(set)
        ref_rows: List[Tuple[str, str, int, int]] = []

        for caller, called in pending_calls:
            target_ids: List[str] = []
            if called in symbol_map:
                target_ids = [called]
            else:
                target_ids = short_name_index.get(called) or suffix_index.get(called) or []

            if not target_ids:
                continue

            for target_id in target_ids:
                updates[target_id].add(caller)

                caller_row = symbol_map.get(caller)
                target_row = symbol_map.get(target_id)
                if caller_row and target_row:
                    caller_file_id = caller_row["file_id"]
                    called_file_id = target_row["file_id"]
                    if caller_file_id is not None and called_file_id is not None:
                        ref_rows.append((caller, target_id, caller_file_id, called_file_id))

        for symbol_id, callers in updates.items():
            row = symbol_map.get(symbol_id)
            if not row:
                continue
            existing = []
            if row["called_by"]:
                try:
                    existing = json.loads(row["called_by"])
                except json.JSONDecodeError:
                    existing = []
            merged = list(dict.fromkeys(existing + list(callers)))
            conn.execute(
                "UPDATE symbols SET called_by=? WHERE symbol_id=?",
                (json.dumps(merged), symbol_id),
            )

        if ref_rows:
            conn.executemany(
                """
                INSERT OR IGNORE INTO refs(
                    caller_symbol_id,
                    called_symbol_id,
                    caller_file_id,
                    called_file_id
                ) VALUES (?, ?, ?, ?)
                """,
                ref_rows,
            )
