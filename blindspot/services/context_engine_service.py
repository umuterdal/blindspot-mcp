"""Unified context engine for AI coding agents.

This service exposes a small, stable contract for understanding a project,
file, symbol, and change impact without forcing the agent to choose among
dozens of specialized tools.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .base_service import BaseService
from .code_intelligence_service import CodeIntelligenceService
from .generic_intelligence_service import GenericIntelligenceService
from ..adapters.project_structure import get_project_structure


# Constructor / lifecycle hooks that exist on nearly every class and
# therefore carry no informational value when used as a grouping key
# for file-level ripple reasons. Filtering them prevents every class
# in the project from being falsely flagged as "related" because they
# all happen to define ``__construct`` / ``__init__``.
#
# FP guard: these names do remain in the symbol index and in direct
# call edges. We only suppress them from the ``file_impact`` fan-out
# pathway that groups files by shared symbol name.
# FN guard: if the user is actually editing a ctor itself, the target
# symbol resolver still finds it; callers continue to surface normally.
_GENERIC_HOOK_NAMES = frozenset({
    "__construct",
    "__destruct",
    "__init__",
    "__new__",
    "__call__",
    "__call",
    "constructor",
    # Synthetic per-file caller emitted by the PHP strategy for
    # top-level scripts. Carries no symbol semantics on its own, so it
    # must not surface as a "related file reason" even though it is a
    # real edge in the refs table.
    "__file_scope__",
})

_QUERY_AUXILIARY_TOKENS = {
    "test": frozenset({"test", "tests", "spec", "specs"}),
    "fixture": frozenset({"fixture", "fixtures", "eval", "evals"}),
    "example": frozenset({"example", "examples", "sample", "samples"}),
    "docs": frozenset({"doc", "docs", "documentation"}),
    "generated": frozenset({"generated", "build", "dist", "cache", "output"}),
}

_QUERY_PATH_ROLE_PRIORITY = {
    "source": 5,
    "test": 4,
    "docs": 3,
    "example": 2,
    "fixture": 1,
    "generated": 0,
}

_LANGUAGE_HINT_TOKENS = frozenset({
    "php", "python", "javascript", "typescript", "java", "kotlin",
    "dart", "flutter", "go", "golang", "zig", "csharp", "node",
    "nodejs", "reactnative",
})


def _is_generic_hook(symbol_name: Optional[str]) -> bool:
    if not symbol_name:
        return False
    short = symbol_name.rsplit(".", 1)[-1]
    return short in _GENERIC_HOOK_NAMES


def _tokenize_text(text: Optional[str]) -> List[str]:
    return [token.lower() for token in re.findall(r"\w+", text or "", flags=re.UNICODE)]


def _compact_token(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _classify_path_role(file_path: Optional[str]) -> str:
    normalized = (file_path or "").replace("\\", "/").lower()
    if not normalized:
        return "source"
    segments = [segment for segment in normalized.split("/") if segment]
    segment_set = set(segments)
    if ".blindspot" in segment_set:
        return "generated"
    if segment_set.intersection({"test", "tests", "__tests__", "spec", "specs"}):
        return "test"
    if segment_set.intersection({"evals", "fixtures"}):
        return "fixture"
    if segment_set.intersection({"example", "examples", "sample", "samples"}):
        return "example"
    if segment_set.intersection({"doc", "docs"}):
        return "docs"
    return "source"


class ContextEngineService(BaseService):
    """Small, language-agnostic context surface for AI coding agents."""

    VALID_INTENTS = {"project", "file", "symbol", "before_edit", "impact"}
    VALID_CHANGE_TYPES = {"modify", "rename", "delete", "signature_change", "contract_change"}

    def __init__(self, ctx):
        super().__init__(ctx)
        self._generic = GenericIntelligenceService(ctx)
        self._code = CodeIntelligenceService(ctx)

    def get_context(
        self,
        target: Optional[str] = None,
        intent: str = "before_edit",
        symbol: Optional[str] = None,
        include_source: bool = True,
        max_related: int = 10,
        change_type: str = "modify",
        owner: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return normalized project/code context for the requested intent.

        When ``query`` is supplied and ``target`` is not, the engine runs a
        BM25 search over indexed symbols and resolves target/symbol/owner
        from the top-ranked match. The resolution details are echoed back
        under ``query_resolution`` so callers can verify the inference.
        """
        resolved_from_query: Optional[Dict[str, Any]] = None
        if query and not target:
            resolved_from_query = self._resolve_query_to_target(query)
            if resolved_from_query:
                target = resolved_from_query.get("target") or target
                symbol = resolved_from_query.get("symbol") or symbol
                owner = resolved_from_query.get("owner") or owner

        normalized_intent = (intent or "before_edit").strip().lower()
        if normalized_intent not in self.VALID_INTENTS:
            return {
                "status": "error",
                "message": f"Unsupported intent '{intent}'. Valid intents: {sorted(self.VALID_INTENTS)}",
            }
        normalized_change_type = (change_type or "modify").strip().lower()
        if normalized_change_type not in self.VALID_CHANGE_TYPES:
            return {
                "status": "error",
                "message": (
                    f"Unsupported change_type '{change_type}'. "
                    f"Valid change types: {sorted(self.VALID_CHANGE_TYPES)}"
                ),
            }

        structure = self._get_structure()
        if not structure:
            return {"status": "error", "message": "Project path not set"}

        response: Dict[str, Any] = {
            "status": "success",
            "intent": normalized_intent,
            "change_type": normalized_change_type,
            "project": {
                "root_name": os.path.basename(structure.project_path.rstrip(os.sep)),
                "language": structure.language or "unknown",
                "framework": structure.framework or "none",
                "scan_dirs": structure.scan_dirs,
            },
            "target": {
                "file_path": target,
                "symbol": symbol,
                "canonical_symbol": None,
                "category": structure.categorize_file(target) if target else None,
            },
            "overview": "",
            "file_context": {},
            "symbol_context": {},
            "relationship_context": {},
            "impact_context": {},
            "direct_callers": [],
            "indirect_dependents": [],
            "blast_radius": {},
            "risk_reasons": [],
            "safe_edit_hints": [],
            "related_files": [],
            "related_file_reasons": [],
            "missing_context": [],
            "confidence": "high",
            "confidence_details": {},
            "edit_plan": {},
            "suggested_next_steps": [],
        }

        if resolved_from_query:
            response["query_resolution"] = resolved_from_query

        if normalized_intent == "project":
            snapshot = self._generic.get_project_snapshot()
            response["project_snapshot"] = self._compact_project_snapshot(snapshot)
            response["overview"] = self._build_project_overview(snapshot, structure)
            response["suggested_next_steps"] = [
                "Use get_context(target='path/to/file', intent='before_edit') before modifying important code.",
                "Use search_code() only when the returned related_files are not enough.",
            ]
            return response

        if not target:
            if query:
                return {
                    "status": "needs_target",
                    "message": (
                        "No indexed symbol matched the query strongly enough to "
                        "resolve a target. Run build_deep_index() or refine the query."
                    ),
                    "query": query,
                    "query_candidates": self._list_query_candidates(query),
                }
            return {
                "status": "error",
                "message": "target is required for intents: file, symbol, before_edit, impact",
            }

        full_path = os.path.join(structure.project_path, target)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {target}"}

        base_context = self._generic.get_context_for_edit(target, symbol if symbol else None)
        if base_context.get("status") != "success":
            return base_context

        file_summary = self._code.analyze_file(target)
        if file_summary.get("status") == "needs_deep_index":
            response["missing_context"].append("deep_index")

        response["file_context"] = self._build_file_context(target, structure, base_context, file_summary)

        if normalized_intent in {"symbol", "before_edit", "impact"} and symbol:
            response["symbol_context"] = self._build_symbol_context(
                target=target,
                symbol=symbol,
                include_source=include_source,
                max_related=max_related,
                owner=owner,
            )
            if not response["symbol_context"].get("found", False):
                response["missing_context"].append(f"symbol:{symbol}")

            symbol_change = self._generic.get_symbol_change_context(
                target,
                symbol,
                change_type=normalized_change_type,
                max_related=max_related,
                owner=owner,
            )
            if symbol_change.get("status") == "success":
                response["target"]["canonical_symbol"] = symbol_change.get(
                    "canonical_symbol",
                    response["symbol_context"].get("canonical_name"),
                )
                response["direct_callers"] = symbol_change.get("direct_callers", [])
                response["indirect_dependents"] = symbol_change.get("indirect_dependents", [])
                response["blast_radius"] = symbol_change.get("blast_radius", {})
                response["risk_reasons"] = symbol_change.get("risk_reasons", [])
                response["safe_edit_hints"] = symbol_change.get("safe_edit_hints", [])

        response["relationship_context"] = self._build_relationship_context(
            base_context=base_context,
            symbol=symbol,
            max_related=max_related,
            direct_callers=response["direct_callers"],
            indirect_dependents=response["indirect_dependents"],
            definition_file=target,
        )
        response["impact_context"] = self._build_impact_context(
            base_context=base_context,
            intent=normalized_intent,
            symbol=symbol,
        )
        response["related_files"] = self._collect_related_files(
            relationship_context=response["relationship_context"],
            impact_context=response["impact_context"],
            direct_callers=response["direct_callers"],
            indirect_dependents=response["indirect_dependents"],
            max_related=max_related,
            target=target,
        )
        response["related_file_reasons"] = self._build_related_file_reasons(
            target=target,
            symbol_context=response["symbol_context"],
            relationship_context=response["relationship_context"],
            impact_context=response["impact_context"],
            direct_callers=response["direct_callers"],
            indirect_dependents=response["indirect_dependents"],
            max_related=max_related,
        )
        self._append_cochange_risk(response, target)
        response["confidence_details"] = self._build_confidence_details(response)
        response["confidence"] = response["confidence_details"]["level"]
        response["edit_plan"] = self._build_edit_plan(response)
        response["overview"] = self._build_target_overview(response)
        response["suggested_next_steps"] = self._build_next_steps(
            intent=normalized_intent,
            has_symbol=bool(symbol),
            missing_context=response["missing_context"],
            change_type=normalized_change_type,
        )
        return response

    def _get_structure(self):
        base = self.base_path
        if not base or not os.path.isdir(base):
            return None
        return get_project_structure(base)

    def _compact_project_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if snapshot.get("status") != "success":
            return snapshot

        return {
            "metrics": snapshot.get("metrics", {}),
            "hotspots": snapshot.get("hotspots", [])[:10],
            "top_classes": snapshot.get("classes", [])[:20],
            "import_graph": snapshot.get("import_graph", {}),
        }

    def _build_project_overview(self, snapshot: Dict[str, Any], structure) -> str:
        metrics = snapshot.get("metrics", {})
        total_files = metrics.get("total_files", 0)
        by_category = metrics.get("by_category", {})
        category_summary = ", ".join(
            f"{name}:{count}" for name, count in sorted(by_category.items())[:4]
        )
        if not category_summary:
            category_summary = "no categorized directories detected"
        return (
            f"{structure.language or 'unknown'} / {structure.framework or 'none'} project with "
            f"{total_files} indexed source files. Primary categories: {category_summary}."
        )

    def _build_file_context(
        self,
        target: str,
        structure,
        base_context: Dict[str, Any],
        file_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        summary = base_context.get("file_summary", {})
        line_count = summary.get("line_count")
        if not line_count and isinstance(file_summary, dict):
            line_count = file_summary.get("line_count")

        return {
            "file_path": target,
            "category": base_context.get("category") or structure.categorize_file(target) or "other",
            "line_count": line_count,
            "classes": summary.get("classes", [])[:20],
            "functions": summary.get("functions", [])[:20],
            "methods": summary.get("methods", [])[:20],
            "imports": summary.get("imports", [])[:20],
        }

    def _build_symbol_context(
        self,
        target: str,
        symbol: str,
        include_source: bool,
        max_related: int,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = self._code.get_symbol_body(
            target,
            symbol,
            compact=not include_source,
            owner=owner,
        )
        found = body.get("status") == "success"
        result: Dict[str, Any] = {
            "found": found,
            "symbol": symbol,
        }

        if not found:
            result["message"] = body.get("message", f"Symbol '{symbol}' not found")
            return result

        result.update(
            {
                "type": body.get("type"),
                "line": body.get("line"),
                "end_line": body.get("end_line"),
                "signature": body.get("signature"),
                "canonical_name": body.get("canonical_name", symbol),
                "match_type": body.get("match_type", "unknown"),
                "lookup_candidates": self._build_lookup_candidates(symbol, body.get("canonical_name")),
            }
        )

        cross_file_callers = body.get("cross_file_callers", [])[:max_related]
        if cross_file_callers:
            result["callers"] = cross_file_callers
        if include_source and body.get("code"):
            result["source_excerpt"] = body.get("code")
        return result

    def _build_relationship_context(
        self,
        base_context: Dict[str, Any],
        symbol: Optional[str],
        max_related: int,
        direct_callers: Optional[List[Dict[str, Any]]] = None,
        indirect_dependents: Optional[List[Dict[str, Any]]] = None,
        definition_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build relationship_context without duplicating top-level fields.

        ``direct_callers`` and ``indirect_dependents`` live on the root
        response; they are intentionally *not* echoed here. A compact
        ``references`` summary is included only when the symbol produced
        cross-file callers and only if it adds files beyond those already
        in ``direct_callers``.
        """
        relationships: Dict[str, Any] = {}

        class_hierarchy = base_context.get("class_hierarchy")
        if class_hierarchy:
            relationships["class_hierarchy"] = class_hierarchy

        if symbol:
            caller_files = {
                item.get("file") for item in (direct_callers or [])
                if item.get("file")
            }
            refs = self._generic.find_references(symbol, definition_file=definition_file)
            extra_files = [
                {
                    "file": ref.get("file"),
                    "category": ref.get("category"),
                    "count": ref.get("count"),
                }
                for ref in refs.get("references", [])[:max_related]
                if ref.get("file") and ref.get("file") not in caller_files
            ]
            if extra_files:
                relationships["references"] = {
                    "total": refs.get("total", 0),
                    "files": extra_files,
                }

        return relationships

    def _build_impact_context(
        self,
        base_context: Dict[str, Any],
        intent: str,
        symbol: Optional[str],
    ) -> Dict[str, Any]:
        """Build impact_context without duplicating top-level fields.

        Top-level ``blast_radius``, ``risk_reasons`` and ``safe_edit_hints``
        remain the canonical source; they are not echoed back here.
        """
        impact: Dict[str, Any] = {}
        impact_summary = base_context.get("impact_summary")
        if impact_summary:
            impact["file_impact"] = impact_summary

        ripple = base_context.get("ripple_effect")
        if symbol and ripple and intent in {"before_edit", "impact", "symbol"}:
            impact["symbol_impact"] = ripple

        return impact

    def _build_related_file_reasons(
        self,
        target: str,
        symbol_context: Dict[str, Any],
        relationship_context: Dict[str, Any],
        impact_context: Dict[str, Any],
        max_related: int,
        direct_callers: Optional[List[Dict[str, Any]]] = None,
        indirect_dependents: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        reasons: Dict[str, Dict[str, Any]] = {
            target: {
                "file": target,
                "role": "definition",
                "reason": (
                    f"Defines symbol '{symbol_context.get('canonical_name') or symbol_context.get('symbol')}'."
                    if symbol_context.get("found")
                    else "Primary file under edit."
                ),
                "priority": 100,
            }
        }

        for item in direct_callers or []:
            file_path = item.get("file")
            if not file_path:
                continue
            strongest = item.get("strongest_usage", "reference")
            # Split file-scope (synthetic PHP ``__file_scope__``) callers
            # into a distinct role. They carry a real cross-file edge
            # but represent procedural entry-point wiring rather than an
            # in-method invocation, so the agent should weigh them lower
            # than a normal method_call direct_caller when sequencing an
            # edit.
            if strongest == "module_script":
                reasons[file_path] = {
                    "file": file_path,
                    "role": "file_scope_caller",
                    "reason": (
                        "References target from a top-level/module script "
                        "(procedural entry-point, not inside a function)."
                    ),
                    "priority": 80,
                }
            else:
                reasons[file_path] = {
                    "file": file_path,
                    "role": "direct_caller",
                    "reason": f"Calls or references the target symbol directly via {strongest}.",
                    "priority": 90,
                }

        for item in indirect_dependents or []:
            file_path = item.get("file")
            if not file_path:
                continue
            via = ", ".join(item.get("via", [])[:2]) or "an intermediate caller"
            reasons[file_path] = {
                "file": file_path,
                "role": "indirect_dependent",
                "reason": f"Depends on a direct caller through {via}.",
                "priority": 70,
            }

        for file_path in impact_context.get("symbol_impact", {}).get("top_files", []) or []:
            if file_path and file_path not in reasons:
                reasons[file_path] = {
                    "file": file_path,
                    "role": "ripple",
                    "reason": "Appears in the ripple-effect summary for this symbol.",
                    "priority": 60,
                }

        for item in impact_context.get("file_impact", {}).get("top_symbols", []) or []:
            symbol_name = item.get("symbol")
            # Skip generic lifecycle hooks: every class declares
            # ``__construct`` / ``__init__``, so grouping files by that
            # shared symbol name produces only noise.
            if _is_generic_hook(symbol_name):
                continue
            for file_path in item.get("files", []) or []:
                if file_path and file_path not in reasons:
                    reasons[file_path] = {
                        "file": file_path,
                        "role": "file_impact",
                        "reason": f"References file-level symbol '{symbol_name}'.",
                        "priority": 50,
                    }

        # Co-change signal: files that historically move together with the
        # target. Lower priority than direct/indirect call graph but strong
        # enough to surface tests & siblings the static graph cannot see.
        for peer in self._cochange_peers(target, limit=max_related):
            file_path = peer.get("peer") or peer.get("file")
            if not file_path or file_path in reasons:
                continue
            count = peer.get("count", 0)
            reasons[file_path] = {
                "file": file_path,
                "role": "co_change",
                "reason": (
                    f"Changed together with the target in {count} recent commits "
                    f"(last {peer.get('last_seen', 'unknown')})."
                ),
                "priority": 40,
            }

        # Semantic fallback: when the structural + co-change signal is thin
        # (very few related files found) blend in BM25 top hits so the agent
        # is not left blind. Only kicks in when the structural lane is weak
        # to avoid polluting strong results with tangential matches.
        structural_count = sum(
            1 for r in reasons.values()
            if r.get("role") in {"direct_caller", "indirect_dependent", "ripple", "file_impact"}
        )
        if structural_count < 3:
            for hit in self._semantic_peers(target, symbol_context, limit=max_related):
                file_path = hit.get("file")
                if not file_path or file_path in reasons:
                    continue
                reasons[file_path] = {
                    "file": file_path,
                    "role": "semantic_related",
                    "reason": (
                        f"Semantically related to the target "
                        f"(match '{hit.get('short_name')}', score {hit.get('score')})."
                    ),
                    "priority": 30,
                }

        ranked = sorted(
            reasons.values(),
            key=lambda item: (int(item.get("priority", 0)), item.get("file", "")),
            reverse=True,
        )
        return ranked[:max_related]

    def _semantic_peers(
        self,
        target: str,
        symbol_context: Dict[str, Any],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """BM25 (+ optional embeddings) top hits for the target's concept.

        Query is derived from the canonical symbol name and any docstring
        fragment. Hits inside the target file itself are filtered out.

        Risk notes:
            False positives: shared keywords (e.g. "save", "parse") pull
                unrelated modules. Mitigated by limiting invocation to the
                "structural lane is thin" case and by keeping role label
                distinct so agents can discount accordingly.
            False negatives: requires the search index to be built; if
                empty, returns [].
        """
        mgr = self.index_manager
        search = getattr(mgr, "search_symbols", None)
        if not callable(search):
            return []
        parts: List[str] = []
        canonical = symbol_context.get("canonical_name") or symbol_context.get("symbol")
        if canonical:
            parts.append(str(canonical))
        docstring = symbol_context.get("docstring")
        if docstring:
            parts.append(str(docstring)[:160])
        if not parts:
            # Fall back to the basename — still better than nothing.
            parts.append(target.rsplit("/", 1)[-1])
        query = " ".join(parts)
        try:
            ranked = search(query, limit=limit * 2) or []
        except Exception:
            return []
        ranked = self._rerank_query_hits(query, ranked)
        target_role = _classify_path_role(target)
        out: List[Dict[str, Any]] = []
        for hit in ranked:
            file_path = hit.get("file")
            if not file_path or file_path == target:
                continue
            hit_role = hit.get("path_role") or _classify_path_role(file_path)
            if target_role == "source" and hit_role != "source":
                continue
            out.append(hit)
            if len(out) >= limit:
                break
        return out

    def _cochange_peers(self, target: str, limit: int) -> List[Dict[str, Any]]:
        mgr = self.index_manager
        finder = getattr(mgr, "find_cochanged_files", None)
        if not callable(finder):
            return []
        try:
            return finder(target, limit=limit) or []
        except Exception:
            return []

    # Thresholds for the co-change risk signal. Tuned to fire only when
    # the historical ripple is meaningful; see FP/FN note below.
    _COCHANGE_STRONG_PEERS = 5
    _COCHANGE_STRONG_COUNT = 4

    def _append_cochange_risk(
        self,
        response: Dict[str, Any],
        target: str,
    ) -> None:
        """Augment ``risk_reasons`` with a co-change signal.

        Fires when the target has several recent-history peers. Signals to
        agents that the change is likely to ripple into files the static
        call graph cannot see (templates, fixtures, docs, config).

        Risk notes:
            False positive: repos with sweeping formatter / rename commits
                inflate peer counts. Mitigated by MAX_FILES_PER_COMMIT=30
                upstream and by the two-threshold gate (peer count AND
                per-pair count).
            False negative: new repos without history yield no signal.
                We stay silent in that case rather than guessing.
        """
        peers = self._cochange_peers(target, limit=self._COCHANGE_STRONG_PEERS + 2)
        strong = [p for p in peers if int(p.get("count", 0)) >= 2]
        if len(strong) < self._COCHANGE_STRONG_PEERS:
            # Not enough peers to justify a risk entry; also stay silent
            # if there is only one very strong peer (ordinary sibling edit).
            if not strong or max(int(p.get("count", 0)) for p in strong) < self._COCHANGE_STRONG_COUNT:
                return
        top_peers = [p.get("peer") for p in strong[:3] if p.get("peer")]
        existing = list(response.get("risk_reasons") or [])
        reason = (
            f"Target historically co-changes with {len(strong)} file(s) "
            f"in the recent commit window; plan coordinated edits"
        )
        if top_peers:
            reason += f" (top peers: {', '.join(top_peers)})"
        reason += "."
        if reason not in existing:
            existing.append(reason)
        response["risk_reasons"] = existing[:8]

    def _resolve_query_to_target(self, query: str) -> Optional[Dict[str, Any]]:
        """Use BM25 to infer a target file + optional symbol from free text.

        Risk notes:
            False positive: a query whose tokens accidentally dominate an
                unrelated high-score symbol can produce a confidently-
                wrong target. Mitigated by echoing ``alternatives`` so the
                agent can cross-check, and by keeping ``score`` in the
                response so callers can threshold.
            False negative: queries that describe a capability without
                overlapping tokens with any indexed symbol return None.
                Callers fall through to the ``needs_target`` branch with
                a candidate list instead of silently guessing.
        """
        mgr = self.index_manager
        search = getattr(mgr, "search_symbols", None)
        if not callable(search):
            return None
        try:
            ranked = search(query, limit=12) or []
        except Exception:
            return None
        if not ranked:
            return None
        ranked = self._rerank_query_hits(query, ranked)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        score = float(top.get("adjusted_score") or top.get("score") or 0.0)
        second_score = float(second.get("adjusted_score") or second.get("score") or 0.0) if second else 0.0
        score_gap = round(score - second_score, 4)
        score_ratio = round(score / max(second_score, 0.0001), 4) if second_score else None
        top_role = top.get("path_role") or _classify_path_role(top.get("file"))

        # Abstain when the best hit is still weak or the top lane is an
        # auxiliary path whose margin over the next candidate is too thin.
        if score < 0.5:
            return None
        if top_role != "source" and score_gap < 1.0 and (score_ratio is None or score_ratio < 1.35):
            return None
        if second and score_gap < 0.35 and (score_ratio is None or score_ratio < 1.12):
            return None

        short = top.get("short_name") or ""
        inferred_symbol: Optional[str] = None
        inferred_owner: Optional[str] = None
        if "." in short:
            owner_part, _, method = short.rpartition(".")
            inferred_symbol = method
            inferred_owner = owner_part
        else:
            inferred_symbol = short or None
        return {
            "query": query,
            "target": top.get("file"),
            "symbol": inferred_symbol,
            "owner": inferred_owner,
            "score": top.get("score"),
            "adjusted_score": round(score, 4),
            "score_gap": score_gap,
            "score_ratio": score_ratio,
            "path_role": top_role,
            "match_symbol_id": top.get("symbol_id"),
            "alternatives": [
                {
                    "symbol_id": r.get("symbol_id"),
                    "file": r.get("file"),
                    "short_name": r.get("short_name"),
                    "score": r.get("score"),
                    "adjusted_score": r.get("adjusted_score"),
                    "path_role": r.get("path_role"),
                }
                for r in ranked[1:6]
            ],
        }

    def _list_query_candidates(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        mgr = self.index_manager
        search = getattr(mgr, "search_symbols", None)
        if not callable(search):
            return []
        try:
            ranked = search(query, limit=limit) or []
        except Exception:
            return []
        ranked = self._rerank_query_hits(query, ranked)
        return [
            {
                "symbol_id": r.get("symbol_id"),
                "file": r.get("file"),
                "short_name": r.get("short_name"),
                "score": r.get("score"),
                "adjusted_score": r.get("adjusted_score"),
                "path_role": r.get("path_role"),
            }
            for r in ranked
        ]

    def _rerank_query_hits(self, query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not hits:
            return []
        query_tokens = set(_tokenize_text(query))
        query_language_tokens = {token for token in query_tokens if token in _LANGUAGE_HINT_TOKENS}
        wants_auxiliary = {
            role for role, tokens in _QUERY_AUXILIARY_TOKENS.items()
            if query_tokens.intersection(tokens)
        }
        ranked: List[Dict[str, Any]] = []
        for hit in hits:
            row = dict(hit)
            path_role = _classify_path_role(row.get("file"))
            short_name = str(row.get("short_name") or "")
            file_path = str(row.get("file") or "")
            short_tokens = set(_tokenize_text(short_name))
            basename_tokens = set(_tokenize_text(os.path.basename(file_path)))
            compact_short = _compact_token(short_name)
            compact_path = _compact_token(file_path)
            compact_query_tokens = {_compact_token(token) for token in query_tokens if token}
            base_score = float(row.get("score") or 0.0)
            adjusted = base_score

            if path_role == "source":
                adjusted *= 1.12
            elif path_role in wants_auxiliary:
                adjusted *= 1.02
            else:
                adjusted *= {
                    "test": 0.72,
                    "docs": 0.45,
                    "example": 0.35,
                    "fixture": 0.18,
                    "generated": 0.08,
                }.get(path_role, 1.0)

            if short_tokens.intersection(query_tokens):
                adjusted *= 1.1
            elif basename_tokens.intersection(query_tokens):
                adjusted *= 1.03
            if any(token and (token in compact_short or token in compact_path) for token in compact_query_tokens):
                adjusted *= 1.08
            if query_language_tokens:
                language_match = any(
                    token in compact_short or token in compact_path
                    for token in query_language_tokens
                )
                adjusted *= 1.35 if language_match else 0.88

            row["path_role"] = path_role
            row["adjusted_score"] = round(adjusted, 4)
            ranked.append(row)

        ranked.sort(
            key=lambda item: (
                float(item.get("adjusted_score") or 0.0),
                _QUERY_PATH_ROLE_PRIORITY.get(str(item.get("path_role") or "source"), 0),
                str(item.get("short_name") or ""),
            ),
            reverse=True,
        )
        return ranked

    def _collect_related_files(
        self,
        relationship_context: Dict[str, Any],
        impact_context: Dict[str, Any],
        max_related: int,
        direct_callers: Optional[List[Dict[str, Any]]] = None,
        indirect_dependents: Optional[List[Dict[str, Any]]] = None,
        target: Optional[str] = None,
    ) -> List[str]:
        related: List[str] = []
        if target:
            related.append(target)

        for item in direct_callers or []:
            file_path = item.get("file")
            if file_path and file_path not in related:
                related.append(file_path)

        for item in indirect_dependents or []:
            file_path = item.get("file")
            if file_path and file_path not in related:
                related.append(file_path)

        for item in relationship_context.get("references", {}).get("files", []):
            file_path = item.get("file")
            if file_path and file_path not in related:
                related.append(file_path)

        symbol_impact = impact_context.get("symbol_impact", {})
        for file_path in symbol_impact.get("top_files", []) or []:
            if file_path and file_path not in related:
                related.append(file_path)

        file_impact = impact_context.get("file_impact", {})
        for item in file_impact.get("top_symbols", []) or []:
            if _is_generic_hook(item.get("symbol")):
                continue
            for file_path in item.get("files", []) or []:
                if file_path and file_path not in related:
                    related.append(file_path)

        if target and len(related) < max_related:
            for peer in self._cochange_peers(target, limit=max_related):
                file_path = peer.get("peer")
                if file_path and file_path not in related:
                    related.append(file_path)
                    if len(related) >= max_related:
                        break

        return related[:max_related]

    def _build_confidence_details(self, response: Dict[str, Any]) -> Dict[str, Any]:
        missing_context = response.get("missing_context", [])
        symbol_context = response.get("symbol_context", {})
        direct_callers = response.get("direct_callers", []) or []
        indirect_dependents = response.get("indirect_dependents", []) or []
        related_files = response.get("related_files", []) or []
        query_resolution = response.get("query_resolution") or {}
        evidence = {
            "has_symbol_match": bool(symbol_context.get("found")),
            "direct_callers": len(direct_callers),
            "indirect_dependents": len(indirect_dependents),
            "related_files": len(related_files),
            "risk_reasons": len(response.get("risk_reasons", []) or []),
            "has_blast_radius": bool(response.get("blast_radius")),
            "query_resolved": bool(query_resolution),
            "query_path_role": query_resolution.get("path_role"),
        }

        score = 0.45
        if not missing_context:
            score += 0.2
        if evidence["has_symbol_match"]:
            score += 0.15
        if evidence["has_blast_radius"]:
            score += 0.1
        if evidence["direct_callers"]:
            score += min(0.08, evidence["direct_callers"] * 0.03)
        if evidence["indirect_dependents"]:
            score += min(0.05, evidence["indirect_dependents"] * 0.02)
        if evidence["query_resolved"] and evidence["query_path_role"] == "source":
            score += 0.05
        if "deep_index" in missing_context:
            score -= 0.2
        if response.get("target", {}).get("symbol") and not evidence["has_symbol_match"]:
            score -= 0.25
        if evidence["query_resolved"]:
            if evidence["query_path_role"] and evidence["query_path_role"] != "source":
                score -= 0.18
            if float(query_resolution.get("score_gap") or 0.0) < 0.35:
                score -= 0.12
            if query_resolution.get("score_ratio") is not None and float(query_resolution.get("score_ratio") or 0.0) < 1.12:
                score -= 0.08

        score = max(0.0, min(1.0, round(score, 2)))
        if score >= 0.8:
            level = "high"
        elif score >= 0.55:
            level = "medium"
        else:
            level = "low"

        inferred = []
        if evidence["indirect_dependents"]:
            inferred.append("Indirect dependents are inferred from direct-caller expansion.")
        if "deep_index" in missing_context:
            inferred.append("Symbol-level evidence is limited because the deep index is missing.")
        if response.get("target", {}).get("symbol") and not evidence["has_symbol_match"]:
            inferred.append("The requested symbol was not resolved exactly; some impact data may be approximate.")
        if evidence["query_resolved"] and evidence["query_path_role"] != "source":
            inferred.append("Free-text query resolution landed on a non-source path, so the match may reflect supporting code.")
        if evidence["query_resolved"] and float(query_resolution.get("score_gap") or 0.0) < 0.35:
            inferred.append("The free-text query had a thin margin over the next candidate; verify query_resolution before editing.")

        return {
            "level": level,
            "score": score,
            "missing_context": list(missing_context),
            "evidence": evidence,
            "inferred_assumptions": inferred,
        }

    def _build_edit_plan(self, response: Dict[str, Any]) -> Dict[str, Any]:
        target = response.get("target", {})
        change_type = response.get("change_type", "modify")
        symbol_context = response.get("symbol_context", {})
        related_file_reasons = response.get("related_file_reasons", []) or []
        verification_targets = [
            item["file"]
            for item in related_file_reasons
            if item.get("role") in {"direct_caller", "indirect_dependent"}
            and "test" in str(item.get("file", "")).lower()
        ]
        touch_files = [item["file"] for item in related_file_reasons[:6]]

        steps = [f"Edit {target.get('file_path')} first."]
        if symbol_context.get("found"):
            steps.append(
                f"Keep changes scoped to '{symbol_context.get('canonical_name') or symbol_context.get('symbol')}' unless the contract must change."
            )
        if change_type in {"rename", "signature_change", "contract_change"}:
            steps.append("Update all direct callers in the same patch to avoid a half-migrated contract.")
        if response.get("indirect_dependents"):
            steps.append("Re-check indirect dependents after updating direct callers.")
        if verification_targets:
            steps.append("Run targeted tests for the affected verification files before broader checks.")
        else:
            steps.append("Re-read related_files after editing to confirm no important dependency was skipped.")

        coordination = (
            "high"
            if change_type in {"rename", "signature_change", "contract_change", "delete"}
            else response.get("blast_radius", {}).get("risk_level", "low")
        )

        return {
            "change_type": change_type,
            "coordination_level": coordination,
            "primary_file": target.get("file_path"),
            "primary_symbol": target.get("canonical_symbol") or target.get("symbol"),
            "touch_files": touch_files,
            "verification_targets": verification_targets[:6],
            "steps": steps[:5],
        }

    def _build_target_overview(self, response: Dict[str, Any]) -> str:
        target = response.get("target", {})
        file_path = target.get("file_path") or "unknown target"
        category = target.get("category") or "other"
        project = response.get("project", {})
        parts = [
            f"{file_path} ({category}) in a {project.get('language', 'unknown')} / {project.get('framework', 'none')} codebase."
        ]

        symbol_context = response.get("symbol_context", {})
        if symbol_context.get("found"):
            parts.append(
                f"Symbol '{symbol_context.get('symbol')}' is a {symbol_context.get('type', 'symbol')} "
                f"at lines {symbol_context.get('line')}:{symbol_context.get('end_line')}."
            )

        blast_radius = response.get("blast_radius", {})
        if blast_radius:
            parts.append(
                f"Blast radius is {blast_radius.get('risk_level', 'unknown')} for a {response.get('change_type', 'modify')} "
                f"across "
                f"{blast_radius.get('total_files', 0)} files "
                f"({blast_radius.get('direct_files', 0)} direct, {blast_radius.get('indirect_files', 0)} indirect)."
            )

        symbol_impact = response.get("impact_context", {}).get("symbol_impact")
        if symbol_impact:
            parts.append(
                f"Ripple risk is {symbol_impact.get('risk_level', 'unknown')} across "
                f"{symbol_impact.get('total_files', 0)} related files."
            )

        file_impact = response.get("impact_context", {}).get("file_impact")
        if file_impact and not symbol_impact:
            parts.append(
                f"File-level impact is {file_impact.get('risk_level', 'unknown')} across "
                f"{file_impact.get('total_affected', 0)} affected files."
            )

        if response.get("missing_context"):
            parts.append(
                "Missing context: " + ", ".join(response["missing_context"]) + "."
            )

        return " ".join(parts)

    def _build_next_steps(
        self,
        intent: str,
        has_symbol: bool,
        missing_context: List[str],
        change_type: str,
    ) -> List[str]:
        steps: List[str] = []

        if "deep_index" in missing_context:
            steps.append("Run build_deep_index() to unlock symbol-level context and stronger relationship analysis.")

        if intent == "project":
            steps.append("Call get_context() with a file target before changing code.")
        elif has_symbol and intent in {"before_edit", "impact"}:
            steps.append("Inspect direct_callers and related_files before changing the symbol.")
            if change_type in {"rename", "signature_change", "contract_change"}:
                steps.append("Treat this as a coordinated refactor and update all direct callers in the same patch.")
            steps.append("Read risk_reasons and safe_edit_hints before making a cross-file change.")
            steps.append("Use get_edit_region() only for the exact area you plan to modify.")
        else:
            steps.append("Use search_code() if you need more callers or implementation examples.")

        return steps[:3]

    @staticmethod
    def _build_lookup_candidates(requested_symbol: str, canonical_name: Optional[str]) -> List[str]:
        candidates = []
        derived = canonical_name.split(".")[-1] if canonical_name else None
        for name in (requested_symbol, canonical_name, derived):
            if name and name not in candidates:
                candidates.append(name)
        return candidates
