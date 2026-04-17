"""
Lightweight BM25 retrieval over indexed symbols.

Builds a pure-Python inverted index from short_name, signature, docstring,
and file path of every indexed symbol. Stored in the existing SQLite
database so retrieval stays local, deterministic, and dependency-free.

Query path: see ``query_bm25`` which applies the standard BM25 formula
``idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))``
and returns ranked ``symbol_id`` matches.

Risk profile
------------
False positives
    Common short tokens ("save", "find", "get") rank many symbols
    closely; top-K answers can include tangential matches. Mitigated by
    the ``_STOPWORDS`` list, camelCase splitting, and by only using BM25
    to *enrich* ranking — never to drive structural decisions. Callers
    that need stricter output should blend with embeddings via RRF (see
    ``embedding_index``).
False negatives
    Symbols without a docstring and with a generic name may not match
    intent-style queries ("pay the refund"). When the optional embedding
    backend is installed it compensates for this via semantic similarity.
    BM25 alone cannot match concepts that do not share tokens with any
    indexed symbol or file path.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

BM25_K1 = 1.5
BM25_B = 0.75
MIN_TOKEN_LEN = 2
MAX_TOKEN_LEN = 48
TOP_TERMS_PER_DOC = 64

# Split on non-alphanumerics, then split camelCase and snake_case
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Conservative stop-list; code-specific noise words.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into", "not",
    "def", "class", "function", "return", "self", "init", "get", "set",
    "var", "let", "const", "new", "if", "else", "while", "for",
    "public", "private", "protected", "static", "void", "final",
    "async", "await", "true", "false", "null", "none", "nil",
})


def tokenize(text: str) -> List[str]:
    """Produce BM25-friendly tokens from a raw chunk of text."""
    if not text:
        return []
    tokens: List[str] = []
    for raw in _TOKEN_SPLIT.split(text):
        if not raw:
            continue
        # Split camelCase
        for piece in _CAMEL_BOUNDARY.split(raw):
            piece = piece.lower()
            if MIN_TOKEN_LEN <= len(piece) <= MAX_TOKEN_LEN and piece not in _STOPWORDS:
                tokens.append(piece)
    return tokens


def _compose_doc_tokens(short_name: str, signature: str, docstring: str, path: str) -> List[str]:
    """Build the token stream for one symbol's BM25 document with per-field weights.

    Rationale
    ---------
    Raw concatenation treats every field equally, which lets 8 methods of
    the same class flood the top-K whenever a query overlaps the owner or
    file-path tokens (``ProviderTokenVerifierService.*`` dominating a
    ``provider token`` query). Weighting the fields stops that cluster
    effect:

    * method-name (after last ``.``) — **2x**: canonical API identity.
    * owner (class name before ``.``) — 1x: contextual.
    * signature + docstring — 1x: secondary evidence.
    * file path — **0.5x**: weak signal; dampened via token-index stride.

    FP guard: still indexes every field so classic code queries keep
    matching. FN guard: owner tokens stay present so ``UserService``
    queries still reach any of its methods.
    """
    tokens: List[str] = []
    method_part = short_name.rsplit('.', 1)[-1] if short_name and '.' in short_name else (short_name or "")
    owner_part = short_name.split('.', 1)[0] if short_name and '.' in short_name else ""

    method_tokens = tokenize(method_part)
    # 2x weight for the method's own name: this is the canonical identity.
    tokens.extend(method_tokens)
    tokens.extend(method_tokens)

    if owner_part:
        tokens.extend(tokenize(owner_part))

    if signature:
        tokens.extend(tokenize(signature))
    if docstring:
        tokens.extend(tokenize(docstring))

    # Path gets half weight via stride sampling: keeps the tokens in the
    # vocabulary (so file-name queries still resolve) but halves their
    # per-doc term frequency to curb path-dominated false positives.
    path_tokens = tokenize(path) if path else []
    if path_tokens:
        tokens.extend(path_tokens[::2])

    return tokens


def build_search_index(conn) -> None:
    """Populate search_docs and search_postings from the symbols table."""
    conn.execute("DELETE FROM search_postings")
    conn.execute("DELETE FROM search_docs")
    conn.execute("DELETE FROM search_stats")

    # Exclude synthetic module-level pseudo-symbols (e.g. the PHP
    # ``__file_scope__`` caller) from BM25 ingestion. They carry no
    # identifier-level meaning and would only add noise to query
    # results; they remain available for cross-file edge tracking in
    # the refs table.
    rows = list(conn.execute(
        """
        SELECT s.symbol_id, s.file_id, s.short_name, s.signature,
               s.docstring, f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type != 'module'
        """
    ))
    if not rows:
        return

    doc_rows: List[Tuple[str, int, str, int]] = []
    posting_rows: List[Tuple[str, str, int]] = []
    doc_freq: Dict[str, int] = defaultdict(int)
    total_len = 0

    for row in rows:
        tokens = _compose_doc_tokens(
            row["short_name"] or "",
            row["signature"] or "",
            row["docstring"] or "",
            row["path"] or "",
        )
        if not tokens:
            continue
        doc_length = len(tokens)
        total_len += doc_length
        tf = Counter(tokens)
        # Cap extreme documents to keep the postings bounded
        if len(tf) > TOP_TERMS_PER_DOC:
            most_common = dict(tf.most_common(TOP_TERMS_PER_DOC))
            tf = Counter(most_common)
        doc_rows.append((
            row["symbol_id"],
            row["file_id"],
            json.dumps(sorted(tf.keys())),
            doc_length,
        ))
        for term, count in tf.items():
            posting_rows.append((term, row["symbol_id"], count))
            doc_freq[term] += 1

    if not doc_rows:
        return

    conn.executemany(
        "INSERT INTO search_docs(symbol_id, file_id, tokens, doc_length) VALUES(?, ?, ?, ?)",
        doc_rows,
    )
    conn.executemany(
        "INSERT INTO search_postings(term, symbol_id, tf) VALUES(?, ?, ?)",
        posting_rows,
    )
    conn.executemany(
        "INSERT INTO search_stats(key, value) VALUES(?, ?)",
        [
            ("doc_count", str(len(doc_rows))),
            ("avg_doc_length", str(total_len / max(1, len(doc_rows)))),
            ("bm25_k1", str(BM25_K1)),
            ("bm25_b", str(BM25_B)),
        ],
    )


def query_bm25(conn, query: str, limit: int = 20) -> List[Tuple[str, float]]:
    """Return ranked ``(symbol_id, score)`` matches for a free-text query."""
    terms = tokenize(query)
    if not terms:
        return []

    stats = _load_stats(conn)
    if not stats:
        return []
    doc_count = stats["doc_count"]
    avgdl = stats["avg_doc_length"] or 1.0

    # Fetch doc_length per candidate document
    term_placeholders = ",".join(["?"] * len(set(terms)))
    term_params = list(set(terms))
    postings = list(conn.execute(
        f"SELECT term, symbol_id, tf FROM search_postings "
        f"WHERE term IN ({term_placeholders})",
        term_params,
    ))
    if not postings:
        return []

    doc_lengths: Dict[str, int] = {}
    candidates = {p["symbol_id"] for p in postings}
    if candidates:
        dl_rows = conn.execute(
            f"SELECT symbol_id, doc_length FROM search_docs "
            f"WHERE symbol_id IN ({','.join(['?'] * len(candidates))})",
            list(candidates),
        )
        for r in dl_rows:
            doc_lengths[r["symbol_id"]] = r["doc_length"]

    term_df: Dict[str, int] = defaultdict(int)
    postings_by_term: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for p in postings:
        term_df[p["term"]] += 1
        postings_by_term[p["term"]].append((p["symbol_id"], p["tf"]))

    scores: Dict[str, float] = defaultdict(float)
    for term in set(terms):
        df = term_df.get(term, 0)
        if df == 0:
            continue
        idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
        for symbol_id, tf in postings_by_term[term]:
            dl = doc_lengths.get(symbol_id, int(avgdl))
            denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
            scores[symbol_id] += idf * tf * (BM25_K1 + 1) / denom

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:limit]


def _load_stats(conn) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in conn.execute("SELECT key, value FROM search_stats"):
        try:
            out[row["key"]] = float(row["value"])
        except (ValueError, TypeError):
            continue
    return out
