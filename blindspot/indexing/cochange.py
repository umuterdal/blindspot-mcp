"""
Git co-change signal extraction.

Parses `git log` over a bounded window of commits and records per-file-pair
co-change counts. The signal is used by the context engine to surface
"files that historically move together" alongside the static call graph.

Risk profile
------------
False positives
    Tree-wide refactors (formatter, rename sweeps, license headers) connect
    hundreds of files in a single commit. ``MAX_FILES_PER_COMMIT`` caps the
    damage; commits touching more files are discarded entirely. Paired
    files that ride along once in an ordinary commit still bubble up —
    the risk-reasons gate in the service requires multiple peers AND a
    minimum per-pair count to fire.
False negatives
    Brand-new repos or shallow clones have no history so the signal is
    silent. We do not fabricate co-change data in that case. Files that
    move exclusively outside the scanned window are also invisible.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DEFAULT_COMMIT_WINDOW = 500
# Skip commits touching more than this many files — they are almost always
# tree-wide refactors or vendored imports and pollute the co-change signal.
MAX_FILES_PER_COMMIT = 30


def collect_cochanges(
    project_path: str,
    commit_window: int = DEFAULT_COMMIT_WINDOW,
    indexed_files: Optional[Set[str]] = None,
) -> Tuple[Dict[Tuple[str, str], int], Dict[Tuple[str, str], str]]:
    """Return (pair_counts, pair_last_seen) for files co-changed in recent commits.

    Args:
        project_path: Absolute path to the repository root.
        commit_window: Number of most recent commits to scan.
        indexed_files: Optional allow-list of relative paths. When supplied,
            pairs where neither side is indexed are discarded.

    Returns:
        A tuple ``(counts, last_seen)``:
        - ``counts`` maps sorted ``(file_a, file_b)`` tuples to co-change count.
        - ``last_seen`` maps the same key to the ISO date of the most recent
          commit touching the pair.
    """
    if not _is_git_repo(project_path):
        logger.debug("No .git directory at %s; skipping co-change", project_path)
        return {}, {}

    commits = _read_git_log(project_path, commit_window)
    if not commits:
        return {}, {}

    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    last_seen: Dict[Tuple[str, str], str] = {}

    for commit_date, files in commits:
        if not files or len(files) > MAX_FILES_PER_COMMIT:
            continue
        filtered = _filter_files(files, indexed_files)
        if len(filtered) < 2:
            continue
        filtered.sort()
        for i in range(len(filtered)):
            for j in range(i + 1, len(filtered)):
                key = (filtered[i], filtered[j])
                counts[key] += 1
                # First occurrence wins — log is scanned newest-first.
                if key not in last_seen:
                    last_seen[key] = commit_date

    return dict(counts), last_seen


def write_cochanges(conn, counts: Dict[Tuple[str, str], int],
                    last_seen: Dict[Tuple[str, str], str]) -> None:
    """Replace the ``cochanges`` table contents with supplied data."""
    conn.execute("DELETE FROM cochanges")
    if not counts:
        return
    rows = [
        (a, b, c, last_seen.get((a, b)))
        for (a, b), c in counts.items()
    ]
    conn.executemany(
        "INSERT INTO cochanges(file_a, file_b, count, last_seen) VALUES(?, ?, ?, ?)",
        rows,
    )


def _is_git_repo(project_path: str) -> bool:
    return os.path.isdir(os.path.join(project_path, ".git"))


def _read_git_log(project_path: str, commit_window: int) -> List[Tuple[str, List[str]]]:
    """Return a list of (commit_date, changed_files) tuples, newest first."""
    cmd = [
        "git", "-C", project_path, "log",
        f"-n{commit_window}",
        "--name-only",
        "--pretty=format:%x1fCOMMIT%x1f%ai",
        "--no-merges",
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=15, text=True,
            errors="replace",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("git log failed for %s: %s", project_path, exc)
        return []

    commits: List[Tuple[str, List[str]]] = []
    current_date: Optional[str] = None
    current_files: List[str] = []
    # Note: do NOT call str.strip() here — Unicode whitespace semantics
    # strip the 0x1F record separator we use as a delimiter.
    for line in out.splitlines():
        if line.startswith("\x1fCOMMIT\x1f"):
            if current_date is not None:
                commits.append((current_date, current_files))
            parts = line.split("\x1f", 2)
            current_date = parts[2].split(" ", 1)[0] if len(parts) >= 3 else ""
            current_files = []
        elif line.strip():
            current_files.append(line.strip())
    if current_date is not None:
        commits.append((current_date, current_files))
    return commits


def _filter_files(files: Iterable[str], indexed_files: Optional[Set[str]]) -> List[str]:
    result: List[str] = []
    for f in files:
        if not f:
            continue
        # Normalise separators to forward slash to match stored paths.
        norm = f.replace("\\", "/")
        if indexed_files is not None and norm not in indexed_files:
            continue
        result.append(norm)
    return result
