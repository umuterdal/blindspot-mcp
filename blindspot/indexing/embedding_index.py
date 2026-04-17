"""
Optional dense-vector retrieval layer.

When ``fastembed`` is installed, this module builds a per-symbol embedding
index alongside the BM25 one. Retrieval blends BM25 and cosine-similarity
scores via Reciprocal Rank Fusion so the two signals reinforce each other.

The module is strictly opt-in: it is skipped when the backend is absent
or when ``BLINDSPOT_ENABLE_EMBEDDINGS`` is not truthy. This keeps the
baseline install dependency-free while letting power users enable the
stronger semantic lane.

Risk profile
------------
False positives
    Small models (BGE-small, MiniLM) cluster tokens by surface semantics;
    "user auth" and "user profile" can collide. RRF with BM25 is the
    primary defence: lexical evidence must agree before a hit rises to
    the top of the blended ranking.
False negatives
    Embeddings only exist for symbols indexed after opt-in. A cold repo
    or missing model download silently yields zero vectors. Detection is
    via ``embeddings_enabled()`` — callers must not assume the index
    exists and fall back to BM25 gracefully, which is what
    ``SQLiteIndexManager.search_symbols`` already does.
    Long docstrings beyond the model's context window are truncated,
    which may drop distinguishing tail detail.
"""

from __future__ import annotations

import logging
import math
import os
import struct
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get(
    "BLINDSPOT_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"
)
BATCH_SIZE = 64


def embeddings_enabled() -> bool:
    """Return True when the user has opted-in and the backend is available."""
    flag = os.environ.get("BLINDSPOT_ENABLE_EMBEDDINGS", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    return _load_backend() is not None


def _load_backend():
    """Import fastembed lazily. Return a loaded model or None."""
    try:
        from fastembed import TextEmbedding  # type: ignore
    except Exception:
        return None
    try:
        return TextEmbedding(model_name=DEFAULT_MODEL)
    except Exception as exc:
        logger.warning("Failed to load embedding model %s: %s", DEFAULT_MODEL, exc)
        return None


def build_embedding_index(conn) -> int:
    """Compute embeddings for every indexed symbol. Return count written.

    Expects ``embeddings`` table. Skips silently when the backend is
    unavailable or when opt-in is off.
    """
    if not embeddings_enabled():
        logger.debug("Embedding backend disabled; skipping index build")
        return 0
    backend = _load_backend()
    if backend is None:
        return 0

    conn.execute("DELETE FROM embeddings")
    rows = list(conn.execute(
        """
        SELECT s.symbol_id AS symbol_id, s.file_id AS file_id,
               s.short_name AS short_name, s.signature AS signature,
               s.docstring AS docstring, f.path AS path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        """
    ))
    if not rows:
        return 0

    payloads: List[str] = []
    meta: List[Tuple[str, int]] = []
    for row in rows:
        text_parts = []
        for field in ("short_name", "signature", "docstring", "path"):
            val = row[field]
            if val:
                text_parts.append(str(val))
        text = " | ".join(text_parts).strip()
        if not text:
            continue
        payloads.append(text)
        meta.append((row["symbol_id"], row["file_id"]))

    if not payloads:
        return 0

    vectors: List[List[float]] = []
    try:
        for batch in _chunks(payloads, BATCH_SIZE):
            vectors.extend(list(backend.embed(batch)))
    except Exception as exc:
        logger.warning("Embedding computation failed: %s", exc)
        return 0

    dim = len(vectors[0]) if vectors else 0
    to_write = []
    for (symbol_id, file_id), vec in zip(meta, vectors):
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        normed = [v / norm for v in vec]
        blob = struct.pack(f"{dim}f", *normed)
        to_write.append((symbol_id, file_id, DEFAULT_MODEL, dim, blob))
    conn.executemany(
        "INSERT OR REPLACE INTO embeddings(symbol_id, file_id, model, dim, vector) VALUES(?, ?, ?, ?, ?)",
        to_write,
    )
    logger.info("Embedding index built: %s symbols, dim=%s", len(to_write), dim)
    return len(to_write)


def query_embeddings(conn, query: str, limit: int = 20) -> List[Tuple[str, float]]:
    """Return ranked ``(symbol_id, cosine_score)`` for a free-text query.

    Returns an empty list when the backend is unavailable or when the
    embeddings table is empty.
    """
    if not embeddings_enabled():
        return []
    backend = _load_backend()
    if backend is None:
        return []
    try:
        qvec = next(iter(backend.embed([query])))
    except Exception:
        return []
    qnorm = math.sqrt(sum(v * v for v in qvec)) or 1.0
    qvec = [v / qnorm for v in qvec]

    rows = list(conn.execute(
        "SELECT symbol_id, dim, vector FROM embeddings"
    ))
    if not rows:
        return []
    scores: List[Tuple[str, float]] = []
    for row in rows:
        dim = int(row["dim"])
        if dim != len(qvec):
            continue
        vec = struct.unpack(f"{dim}f", row["vector"])
        score = sum(a * b for a, b in zip(qvec, vec))
        scores.append((row["symbol_id"], score))
    scores.sort(key=lambda kv: kv[1], reverse=True)
    return scores[:limit]


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
