import importlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blindspot.adapters.language_syntax import get_language_syntax
from blindspot.adapters.project_structure import get_project_structure
from blindspot.adapters.symbol_resolver import SymbolResolver
from blindspot.services.context_engine_service import ContextEngineService


class _FakeLifespan:
    def __init__(self, base_path: str):
        self.base_path = base_path
        self.settings = None
        self.file_count = 0


class _FakeReqCtx:
    def __init__(self, base_path: str):
        self.lifespan_context = _FakeLifespan(base_path)


class _FakeCtx:
    def __init__(self, base_path: str):
        self.request_context = _FakeReqCtx(base_path)


class ContextEngineServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = _FakeCtx(self.tmp.name)
        self._write_project()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_project(self) -> None:
        with open(os.path.join(self.tmp.name, ".blindspot.yaml"), "w", encoding="utf-8") as f:
            f.write("language: python\n")

        with open(os.path.join(self.tmp.name, "service.py"), "w", encoding="utf-8") as f:
            f.write(
                "class Greeter:\n"
                "    def greet(self, name: str) -> str:\n"
                "        return format_name(name)\n"
                "\n"
                "def format_name(name: str) -> str:\n"
                "    return f'hello {name}'\n"
            )

        with open(os.path.join(self.tmp.name, "main.py"), "w", encoding="utf-8") as f:
            f.write(
                "from service import Greeter\n"
                "\n"
                "def run() -> str:\n"
                "    greeter = Greeter()\n"
                "    return greeter.greet('world')\n"
            )

    def test_project_intent_returns_snapshot(self):
        with patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.get_project_snapshot",
            return_value={
                "status": "success",
                "metrics": {"total_files": 2, "by_category": {"services": 1}},
                "hotspots": [{"file": "service.py", "lines": 20, "symbols": 2}],
                "classes": [{"name": "Greeter", "file": "service.py", "methods": 1}],
                "import_graph": {"main.py": ["service.Greeter"]},
            },
        ):
            result = ContextEngineService(self.ctx).get_context(intent="project")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["intent"], "project")
        self.assertEqual(result["project"]["language"], "python")
        self.assertGreaterEqual(result["project_snapshot"]["metrics"]["total_files"], 2)

    def test_before_edit_symbol_returns_related_files_and_symbol_context(self):
        with patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.get_context_for_edit",
            return_value={
                "status": "success",
                "category": "services",
                "file_summary": {
                    "classes": ["Greeter"],
                    "functions": ["format_name"],
                    "methods": ["Greeter.greet"],
                    "imports": [],
                    "line_count": 6,
                },
                "impact_summary": {
                    "total_affected": 1,
                    "risk_level": "low",
                    "top_symbols": [{"symbol": "greet", "files": ["main.py"]}],
                },
                "ripple_effect": {
                    "risk_level": "low",
                    "total_files": 1,
                    "categories": ["other"],
                    "top_files": ["main.py"],
                },
            },
        ), patch(
            "blindspot.services.context_engine_service.CodeIntelligenceService.analyze_file",
            return_value={"file_path": "service.py", "line_count": 6},
        ), patch(
            "blindspot.services.context_engine_service.CodeIntelligenceService.get_symbol_body",
            return_value={
                "status": "success",
                "type": "method",
                "line": 2,
                "end_line": 3,
                "signature": "def greet(self, name: str) -> str",
                "canonical_name": "Greeter.greet",
                "match_type": "qualified_suffix",
                "cross_file_callers": [{"file": "main.py", "line": 5, "text": "return greeter.greet('world')"}],
            },
        ), patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.find_references",
            return_value={
                "status": "success",
                "total": 1,
                "references": [{"file": "main.py", "category": "other", "count": 1}],
            },
        ), patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.get_symbol_change_context",
            return_value={
                "status": "success",
                "direct_callers": [
                    {
                        "file": "main.py",
                        "category": "other",
                        "count": 1,
                        "strongest_usage": "method_call",
                        "usage_types": ["method_call"],
                        "snippets": [{"line": 5, "type": "method_call", "snippet": "return greeter.greet('world')"}],
                    }
                ],
                "indirect_dependents": [
                    {
                        "file": "tests/test_main.py",
                        "category": "tests",
                        "via": ["main.py"],
                        "symbols": ["run"],
                        "count": 1,
                    }
                ],
                "blast_radius": {
                    "risk_level": "medium",
                    "risk_score": 8.5,
                    "direct_files": 1,
                    "direct_usages": 1,
                    "indirect_files": 1,
                    "total_files": 2,
                    "categories": ["other", "tests"],
                    "high_risk_files": [],
                },
                "risk_reasons": [
                    "Symbol is used directly in 1 files.",
                    "Second-order dependency chain detected through 1 indirect dependents.",
                ],
                "safe_edit_hints": [
                    "Inspect the top direct callers first: main.py.",
                    "Re-check files that depend on direct callers; they can break even if first-order callers compile.",
                ],
            },
        ):
            result = ContextEngineService(self.ctx).get_context(
                target="service.py",
                intent="before_edit",
                symbol="greet",
                include_source=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confidence"], "high")
        self.assertIn("confidence_details", result)
        self.assertEqual(result["confidence_details"]["level"], "high")
        self.assertTrue(result["symbol_context"]["found"])
        self.assertEqual(result["symbol_context"]["type"], "method")
        self.assertEqual(result["symbol_context"]["canonical_name"], "Greeter.greet")
        self.assertIn("main.py", result["related_files"])
        self.assertIn("tests/test_main.py", result["related_files"])
        self.assertGreaterEqual(len(result["related_file_reasons"]), 2)
        self.assertTrue(result["edit_plan"]["steps"])
        self.assertEqual(result["impact_context"]["symbol_impact"]["risk_level"], "low")
        self.assertEqual(result["blast_radius"]["risk_level"], "medium")
        self.assertEqual(result["direct_callers"][0]["file"], "main.py")
        self.assertEqual(result["indirect_dependents"][0]["file"], "tests/test_main.py")
        self.assertGreaterEqual(len(result["risk_reasons"]), 1)
        self.assertGreaterEqual(len(result["safe_edit_hints"]), 1)
        self.assertIn("relationship_buckets", result)
        self.assertIn("service.py", result["relationship_buckets"]["certain"])
        self.assertIn("tests/test_main.py", result["relationship_buckets"]["probable"])
        first_reason = result["related_file_reasons"][0]
        self.assertIn("certainty", first_reason)
        self.assertIn("evidence_type", first_reason)
        self.assertIn("evidence_strength", first_reason)

    def test_signature_change_context_builds_coordinated_edit_plan(self):
        with patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.get_context_for_edit",
            return_value={
                "status": "success",
                "category": "services",
                "file_summary": {"classes": [], "functions": ["format_name"], "methods": [], "imports": [], "line_count": 6},
                "impact_summary": {"total_affected": 1, "risk_level": "low", "top_symbols": [{"symbol": "format_name", "files": ["main.py"]}]},
                "ripple_effect": {"risk_level": "medium", "total_files": 1, "categories": ["other"], "top_files": ["main.py"]},
            },
        ), patch(
            "blindspot.services.context_engine_service.CodeIntelligenceService.analyze_file",
            return_value={"file_path": "service.py", "line_count": 6},
        ), patch(
            "blindspot.services.context_engine_service.CodeIntelligenceService.get_symbol_body",
            return_value={
                "status": "success",
                "type": "function",
                "line": 5,
                "end_line": 6,
                "signature": "def format_name(name: str) -> str",
                "canonical_name": "format_name",
                "match_type": "exact",
                "cross_file_callers": [{"file": "main.py", "line": 5, "text": "return format_name(name)"}],
            },
        ), patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.find_references",
            return_value={"status": "success", "total": 1, "references": [{"file": "main.py", "category": "other", "count": 1}]},
        ), patch(
            "blindspot.services.context_engine_service.GenericIntelligenceService.get_symbol_change_context",
            return_value={
                "status": "success",
                "canonical_symbol": "format_name",
                "direct_callers": [{"file": "main.py", "category": "other", "count": 1, "strongest_usage": "method_call", "usage_types": ["method_call"], "snippets": []}],
                "indirect_dependents": [{"file": "tests/test_main.py", "category": "tests", "via": ["main.py"], "symbols": ["run"], "count": 1}],
                "blast_radius": {"risk_level": "medium", "risk_score": 9.2, "direct_files": 1, "direct_usages": 1, "indirect_files": 1, "total_files": 2, "categories": ["other", "tests"], "high_risk_files": []},
                "risk_reasons": ["Second-order dependency chain detected through 1 indirect dependents."],
                "safe_edit_hints": ["Inspect the top direct callers first: main.py."],
            },
        ):
            result = ContextEngineService(self.ctx).get_context(
                target="service.py",
                intent="before_edit",
                symbol="format_name",
                include_source=False,
                change_type="signature_change",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["change_type"], "signature_change")
        self.assertEqual(result["edit_plan"]["coordination_level"], "high")
        self.assertIn("Update all direct callers in the same patch", " ".join(result["edit_plan"]["steps"]))
        self.assertEqual(result["target"]["canonical_symbol"], "format_name")


class ServerToolSurfaceTests(unittest.TestCase):
    def test_server_exposes_only_core_context_tools(self):
        server = importlib.import_module("blindspot.server")
        server = importlib.reload(server)

        tools = {tool.name for tool in server.mcp._tool_manager.list_tools()}

        self.assertEqual(tools, set(server.CORE_CONTEXT_TOOL_NAMES))
        self.assertIn("get_context", tools)
        self.assertNotIn("safe_implement", tools)
        self.assertNotIn("get_context_for_edit", tools)


class _FakeIndexManager:
    def __init__(self, summaries):
        self._summaries = summaries

    def find_files(self, _pattern):
        return list(self._summaries.keys())

    def get_file_summary(self, rel_path):
        return self._summaries.get(rel_path)


class SymbolResolverDependencyTests(unittest.TestCase):
    def test_symbol_change_context_includes_test_dependents_outside_scan_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, ".blindspot.yaml"), "w", encoding="utf-8") as handle:
                handle.write("language: python\nscan_dirs:\n  app: app\n")

            os.makedirs(os.path.join(tmp, "app"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)

            with open(os.path.join(tmp, "app", "orders.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "class OrderService:\n"
                    "    def total_for(self, subtotal: float, tier: str) -> float:\n"
                    "        return subtotal\n"
                )
            with open(os.path.join(tmp, "app", "api.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "from app.orders import OrderService\n\n"
                    "def quote_total(subtotal: float, tier: str) -> float:\n"
                    "    service = OrderService()\n"
                    "    return service.total_for(subtotal, tier)\n"
                )
            with open(os.path.join(tmp, "tests", "test_orders.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "from app.api import quote_total\n\n"
                    "def test_quote_total_for_vip():\n"
                    "    assert quote_total(100, 'vip') == 100\n"
                )

            structure = get_project_structure(tmp)
            resolver = SymbolResolver(
                tmp,
                structure,
                index_manager=_FakeIndexManager(
                    {
                        "app/orders.py": {
                            "functions": [
                                {"name": "total_for", "line": 2, "end_line": 3, "signature": "def total_for(self, subtotal, tier):"}
                            ],
                            "methods": [],
                            "classes": [{"name": "OrderService", "line": 1, "end_line": 3, "signature": "class OrderService:"}],
                            "imports": [],
                            "line_count": 3,
                        },
                        "app/api.py": {
                            "functions": [
                                {"name": "quote_total", "line": 3, "end_line": 5, "signature": "def quote_total(subtotal, tier):"}
                            ],
                            "methods": [],
                            "classes": [],
                            "imports": ["app.orders.OrderService"],
                            "line_count": 5,
                        },
                        "tests/test_orders.py": {
                            "functions": [{"name": "test_quote_total_for_vip", "line": 3, "end_line": 4, "signature": "def test_quote_total_for_vip():"}],
                            "methods": [],
                            "classes": [],
                            "imports": ["app.api.quote_total"],
                            "line_count": 4,
                        },
                    }
                ),
            )

            result = resolver.get_symbol_change_context("app/orders.py", "total_for")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["direct_callers"][0]["file"], "app/api.py")
            self.assertEqual(result["indirect_dependents"][0]["file"], "tests/test_orders.py")
            self.assertEqual(result["indirect_dependents"][0]["category"], "tests")
            self.assertIn(
                "Second-order dependency chain detected through 1 indirect dependents.",
                result["risk_reasons"],
            )


class MultiLanguageSupportTests(unittest.TestCase):
    def test_flutter_project_structure_detects_framework_and_scan_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "lib", "services"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "lib", "screens"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "test"), exist_ok=True)
            with open(os.path.join(tmp, "pubspec.yaml"), "w", encoding="utf-8") as handle:
                handle.write(
                    "name: sample_app\n"
                    "dependencies:\n"
                    "  flutter:\n"
                    "    sdk: flutter\n"
                )

            structure = get_project_structure(tmp)

            self.assertEqual(structure.language, "dart")
            self.assertEqual(structure.framework, "flutter")
            self.assertIn(".dart", structure.source_extensions)
            self.assertEqual(structure.scan_dirs["services"], "lib/services")
            self.assertEqual(structure.scan_dirs["controllers"], "lib/screens")
            self.assertEqual(structure.scan_dirs["tests"], "test")

    def test_react_native_project_structure_detects_framework_and_navigation_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "screens"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "src", "navigation"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "__tests__"), exist_ok=True)
            with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as handle:
                handle.write(
                    '{'
                    '"dependencies":{"react-native":"0.76.0","react":"19.0.0"}'
                    '}'
                )

            structure = get_project_structure(tmp)

            self.assertEqual(structure.language, "javascript")
            self.assertEqual(structure.framework, "reactnative")
            self.assertEqual(structure.scan_dirs["routes"], "src/navigation")
            self.assertEqual(structure.scan_dirs["controllers"], "src/screens")
            self.assertEqual(structure.scan_dirs["tests"], "__tests__")

    def test_symfony_project_structure_detects_framework(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "Entity"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "src", "Controller"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
            with open(os.path.join(tmp, "composer.json"), "w", encoding="utf-8") as handle:
                handle.write(
                    '{'
                    '"require":{"php":"^8.2","symfony/framework-bundle":"^7.0"}'
                    '}'
                )

            structure = get_project_structure(tmp)

            self.assertEqual(structure.language, "php")
            self.assertEqual(structure.framework, "symfony")
            self.assertEqual(structure.scan_dirs["models"], "src/Entity")
            self.assertEqual(structure.scan_dirs["controllers"], "src/Controller")
            self.assertEqual(structure.scan_dirs["tests"], "tests")

    def test_language_syntax_handles_js_arrow_functions_and_dart_symbols(self):
        js_syntax = get_language_syntax("javascript")
        js_functions = js_syntax.find_function_declarations(
            "export const createRate = async (tier) => tier === 'vip' ? 0.8 : 1.0;\n"
        )
        self.assertEqual(js_functions[0]["name"], "createRate")

        dart_syntax = get_language_syntax("dart")
        dart_content = (
            "import 'package:flutter/widgets.dart';\n"
            "class CheckoutScreen extends StatefulWidget {}\n"
            "double rateFor(String tier) => tier == 'vip' ? 0.8 : 1.0;\n"
        )
        dart_classes = dart_syntax.find_class_declarations(dart_content)
        dart_functions = dart_syntax.find_function_declarations(dart_content)

        self.assertEqual(dart_classes[0]["name"], "CheckoutScreen")
        self.assertEqual(dart_functions[0]["name"], "rateFor")


class _FakeSearchIndexManager:
    """Test-only fake index manager wired into the ctx lifespan context.

    Returns deterministic co-change and BM25 results so the signal-
    enrichment logic in ``ContextEngineService`` can be exercised without
    building a real SQLite index.
    """

    def __init__(self, cochanged=None, search_hits=None):
        self._cochanged = list(cochanged or [])
        self._search_hits = list(search_hits or [])

    def find_cochanged_files(self, _target, limit=8):
        return list(self._cochanged[:limit])

    def search_symbols(self, _query, limit=20):
        return list(self._search_hits[:limit])


def _build_ctx_with_fake_index(base_path, *, cochanged=None, search_hits=None):
    ctx = _FakeCtx(base_path)
    ctx.request_context.lifespan_context.index_manager = _FakeSearchIndexManager(
        cochanged=cochanged, search_hits=search_hits,
    )
    return ctx


class SignalEnrichmentTests(unittest.TestCase):
    """Covers semantic fallback, co-change, free-text query, ranking."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(self.tmp.name, ".blindspot.yaml"), "w", encoding="utf-8") as f:
            f.write("language: python\n")
        with open(os.path.join(self.tmp.name, "service.py"), "w", encoding="utf-8") as f:
            f.write("def greet(name):\n    return 'hello ' + name\n")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- shared mock stubs -------------------------------------------------

    def _patch_structural(self, direct_callers, indirect_dependents, risk_reasons=None):
        """Patch the structural analysis path to return canned results.

        Leaves semantic/cochange untouched so tests can exercise those
        signals independently.
        """
        edit_ctx = {
            "status": "success",
            "category": "services",
            "file_summary": {"classes": [], "functions": ["greet"], "methods": [], "imports": [], "line_count": 2},
            "impact_summary": {"total_affected": 0, "risk_level": "low", "top_symbols": []},
            "ripple_effect": {"risk_level": "low", "total_files": 0, "categories": [], "top_files": []},
        }
        symbol_body = {
            "status": "success",
            "type": "function",
            "line": 1,
            "end_line": 2,
            "signature": "def greet(name)",
            "canonical_name": "greet",
            "match_type": "exact",
            "docstring": None,
        }
        find_refs = {"status": "success", "total": len(direct_callers), "references": []}
        change_ctx = {
            "status": "success",
            "canonical_symbol": "greet",
            "direct_callers": direct_callers,
            "indirect_dependents": indirect_dependents,
            "blast_radius": {"risk_level": "low", "risk_score": 1.0,
                             "direct_files": len(direct_callers),
                             "indirect_files": len(indirect_dependents),
                             "total_files": len(direct_callers) + len(indirect_dependents),
                             "categories": ["other"], "high_risk_files": []},
            "risk_reasons": list(risk_reasons or []),
            "safe_edit_hints": [],
        }
        return (
            patch("blindspot.services.context_engine_service.GenericIntelligenceService.get_context_for_edit",
                  return_value=edit_ctx),
            patch("blindspot.services.context_engine_service.CodeIntelligenceService.analyze_file",
                  return_value={"file_path": "service.py", "line_count": 2}),
            patch("blindspot.services.context_engine_service.CodeIntelligenceService.get_symbol_body",
                  return_value=symbol_body),
            patch("blindspot.services.context_engine_service.GenericIntelligenceService.find_references",
                  return_value=find_refs),
            patch("blindspot.services.context_engine_service.GenericIntelligenceService.get_symbol_change_context",
                  return_value=change_ctx),
        )

    def _run_get_context(self, ctx, **overrides):
        kwargs = {
            "target": "service.py",
            "intent": "before_edit",
            "symbol": "greet",
            "include_source": False,
            "max_related": 10,
        }
        kwargs.update(overrides)
        return ContextEngineService(ctx).get_context(**kwargs)

    # ---- 1. Semantic fallback ---------------------------------------------

    def test_semantic_fallback_appears_when_structural_lane_is_thin(self):
        """FN guard: when structural returns 0 callers, semantic top hits
        must still populate related_file_reasons so the agent is not
        stranded. Role must be ``semantic_related`` — never ``direct_caller``.
        """
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "sid-1", "file": "billing/refund.py",
                 "short_name": "issue_refund", "score": 11.0},
                {"symbol_id": "sid-2", "file": "billing/ledger.py",
                 "short_name": "record_entry", "score": 8.5},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        roles = {r.get("role") for r in result["related_file_reasons"]}
        self.assertIn("semantic_related", roles)
        semantic_files = {
            r["file"] for r in result["related_file_reasons"]
            if r.get("role") == "semantic_related"
        }
        self.assertIn("billing/refund.py", semantic_files)
        # FP guard: target file itself must never be labelled semantic_related
        self.assertNotIn("service.py", semantic_files)

    def test_semantic_fallback_suppressed_when_strong_structural_signal(self):
        """FP guard: 3+ structural hits must suppress the semantic lane so
        tangential BM25 matches do not dilute the direct-impact picture.
        """
        direct_callers = [
            {"file": f"app/caller_{i}.py", "category": "other", "count": 1,
             "strongest_usage": "method_call", "usage_types": ["method_call"], "snippets": []}
            for i in range(3)
        ]
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "noise", "file": "unrelated/module.py",
                 "short_name": "unrelated", "score": 9.0},
            ],
        )
        patches = self._patch_structural(direct_callers=direct_callers, indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        roles = [r.get("role") for r in result["related_file_reasons"]]
        self.assertNotIn("semantic_related", roles)

    # ---- 2. Co-change signal ---------------------------------------------

    def test_cochange_below_threshold_is_quiet_in_risk_reasons(self):
        """FP guard: a single co-changed sibling must not trigger a risk
        entry. Only sustained multi-peer patterns should escalate.
        """
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            cochanged=[{"peer": "tests/test_service.py", "count": 1, "last_seen": "2025-12-01"}],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        joined = " ".join(result.get("risk_reasons", []))
        self.assertNotIn("historically co-changes", joined)

    def test_cochange_above_threshold_adds_risk_and_related_file_entry(self):
        """FN guard: when 5+ peers each with count >= 4 exist, the signal
        must surface in BOTH risk_reasons (so agents plan coordinated
        edits) AND related_file_reasons with role=co_change.
        """
        cochanged = [
            {"peer": f"templates/view_{i}.html", "count": 4 + i, "last_seen": "2025-12-01"}
            for i in range(6)
        ]
        ctx = _build_ctx_with_fake_index(self.tmp.name, cochanged=cochanged)
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        joined = " ".join(result.get("risk_reasons", []))
        self.assertIn("historically co-changes", joined)
        roles = {r.get("role") for r in result["related_file_reasons"]}
        self.assertIn("co_change", roles)

    # ---- 3. Free-text intent resolution ----------------------------------

    def test_query_without_target_resolves_via_bm25_and_echoes_alternatives(self):
        """FN guard: agent supplies natural language only — engine must
        resolve target/symbol from BM25 top hit and expose alternatives
        so the caller can audit the inference.
        """
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "svc-greet", "file": "service.py",
                 "short_name": "greet", "score": 12.3},
                {"symbol_id": "alt", "file": "other.py",
                 "short_name": "something_else", "score": 6.1},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = ContextEngineService(ctx).get_context(
                query="greet user by name",
                intent="before_edit",
                include_source=False,
            )

        self.assertIn("query_resolution", result)
        self.assertEqual(result["query_resolution"]["target"], "service.py")
        self.assertEqual(result["query_resolution"]["symbol"], "greet")
        self.assertGreaterEqual(len(result["query_resolution"].get("alternatives", [])), 1)
        self.assertIn("selection_reason", result["query_resolution"])
        self.assertIn("rejected_reason", result["query_resolution"]["alternatives"][0])

    def test_query_prefers_source_hit_over_fixture_when_scores_are_close(self):
        """FP guard: eval/fixture hits often share many lexical tokens with
        implementation helpers. Query resolution must still prefer a real
        source file over a nearby fixture unless the query explicitly asks
        for fixture/eval content.
        """
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "evals", "fixtures", "ts_case"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "src", "strategy.py"), "w", encoding="utf-8") as handle:
            handle.write("def _capture_constructor_body_assignments():\n    return None\n")
        with open(
            os.path.join(self.tmp.name, "evals", "fixtures", "ts_case", "controller_fixture.py"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("class FixtureController:\n    def __init__(self):\n        pass\n")
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "fixture-top", "file": "evals/fixtures/ts_case/controller_fixture.py",
                 "short_name": "FixtureController.constructor", "score": 9.6},
                {"symbol_id": "source-next", "file": "src/strategy.py",
                 "short_name": "_capture_constructor_body_assignments", "score": 9.1},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = ContextEngineService(ctx).get_context(
                query="typescript constructor body dependency injection call resolution",
                intent="before_edit",
                include_source=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["query_resolution"]["target"],
            "src/strategy.py",
        )
        self.assertEqual(result["query_resolution"]["path_role"], "source")

    def test_query_with_no_match_returns_needs_target_and_candidates(self):
        """FP guard: when BM25 cannot confidently resolve anything, the
        engine must not fabricate a target. It must surface ``needs_target``
        with the raw candidate list so the agent can choose or refine.
        """
        ctx = _build_ctx_with_fake_index(self.tmp.name, search_hits=[])
        result = ContextEngineService(ctx).get_context(
            query="totally unindexed concept xyz",
            intent="before_edit",
        )
        self.assertEqual(result["status"], "needs_target")
        self.assertIn("query", result)
        self.assertIsInstance(result.get("query_candidates", []), list)

    def test_query_abstains_when_top_candidates_are_ambiguous(self):
        """FP guard: if the top two source candidates are too close, the
        engine must not invent a winner. ``needs_target`` is safer than a
        low-margin guess that sends the agent into the wrong file.
        """
        os.makedirs(os.path.join(self.tmp.name, "app"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "app", "auth_service.py"), "w", encoding="utf-8") as handle:
            handle.write("def authenticate(token):\n    return token\n")
        with open(os.path.join(self.tmp.name, "app", "provider_auth.py"), "w", encoding="utf-8") as handle:
            handle.write("def authenticate(token):\n    return token\n")
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "a", "file": "app/auth_service.py",
                 "short_name": "authenticate", "score": 8.0},
                {"symbol_id": "b", "file": "app/provider_auth.py",
                 "short_name": "authenticate", "score": 7.96},
            ],
        )
        result = ContextEngineService(ctx).get_context(
            query="authenticate",
            intent="before_edit",
            include_source=False,
        )
        self.assertEqual(result["status"], "needs_target")
        self.assertGreaterEqual(len(result.get("query_candidates", [])), 2)

    def test_query_prefers_language_matched_source_when_multiple_helpers_exist(self):
        """FN guard: when several source helpers implement the same idea in
        different languages, an explicit language token in the query must
        lift the matching implementation above unrelated languages.
        """
        os.makedirs(os.path.join(self.tmp.name, "blindspot", "indexing", "strategies"), exist_ok=True)
        for filename in ("php_strategy.py", "javascript_strategy.py", "typescript_strategy.py"):
            with open(
                os.path.join(self.tmp.name, "blindspot", "indexing", "strategies", filename),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("def _capture_constructor_body_assignments():\n    return None\n")
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "php", "file": "blindspot/indexing/strategies/php_strategy.py",
                 "short_name": "PHPParsingStrategy._capture_constructor_body_assignments", "score": 17.5},
                {"symbol_id": "js", "file": "blindspot/indexing/strategies/javascript_strategy.py",
                 "short_name": "JavaScriptParsingStrategy._capture_constructor_body_assignments", "score": 17.0},
                {"symbol_id": "ts", "file": "blindspot/indexing/strategies/typescript_strategy.py",
                 "short_name": "TypeScriptParsingStrategy._capture_constructor_body_assignments", "score": 14.7},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = ContextEngineService(ctx).get_context(
                query="typescript constructor body dependency injection call resolution",
                intent="before_edit",
                include_source=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["query_resolution"]["target"],
            "blindspot/indexing/strategies/typescript_strategy.py",
        )

    def test_query_prefers_source_candidate_over_test_candidate_when_query_is_not_test_related(self):
        """FP guard: exact token overlap inside a test symbol must not beat
        the best source implementation unless the query explicitly asks for
        test/spec content.
        """
        os.makedirs(os.path.join(self.tmp.name, "src", "main", "java", "com", "example", "controller"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "src", "test", "java", "com", "example", "controller"), exist_ok=True)
        with open(
            os.path.join(self.tmp.name, "src", "main", "java", "com", "example", "controller", "CheckoutController.java"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("class CheckoutController { double quoteTotal() { return 0; } }\n")
        with open(
            os.path.join(self.tmp.name, "src", "test", "java", "com", "example", "controller", "CheckoutControllerTest.java"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("class CheckoutControllerTest { void testQuoteTotal() {} }\n")
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "test", "file": "src/test/java/com/example/controller/CheckoutControllerTest.java",
                 "short_name": "CheckoutControllerTest.testQuoteTotal", "score": 5.65},
                {"symbol_id": "source", "file": "src/main/java/com/example/controller/CheckoutController.java",
                 "short_name": "CheckoutController.quoteTotal", "score": 5.55},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = ContextEngineService(ctx).get_context(
                query="pricing quote total",
                intent="before_edit",
                include_source=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["query_resolution"]["target"],
            "src/main/java/com/example/controller/CheckoutController.java",
        )

    # ---- 4. Ranking stability --------------------------------------------

    def test_ranking_priority_order_direct_caller_over_all_auxiliary_signals(self):
        """FP guard against ranking drift. When direct_caller,
        indirect_dependent, co_change and semantic_related all coexist,
        their priorities must respect: direct > indirect > co_change >
        semantic. Agents trust the first entries; drift here would
        silently push noisy signals above structural truth.
        """
        direct_callers = [
            {"file": "app/api.py", "category": "other", "count": 1,
             "strongest_usage": "method_call", "usage_types": ["method_call"], "snippets": []},
        ]
        indirect_dependents = [
            {"file": "tests/test_api.py", "category": "tests",
             "via": ["app/api.py"], "symbols": ["run"], "count": 1},
        ]
        # Force semantic_related to qualify by keeping structural hits
        # below the fallback threshold (structural_count < 3: one direct
        # + one indirect = 2, triggers the semantic lane).
        cochanged = [
            {"peer": "docs/api.md", "count": 2, "last_seen": "2025-12-01"},
        ]
        search_hits = [
            {"symbol_id": "hit", "file": "unrelated/helper.py",
             "short_name": "helper", "score": 7.0},
        ]
        ctx = _build_ctx_with_fake_index(
            self.tmp.name, cochanged=cochanged, search_hits=search_hits,
        )
        patches = self._patch_structural(
            direct_callers=direct_callers,
            indirect_dependents=indirect_dependents,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        rr = result["related_file_reasons"]
        # definition is always top (priority 100); find where each role first appears
        role_first_index = {}
        for idx, item in enumerate(rr):
            role = item.get("role")
            if role and role not in role_first_index:
                role_first_index[role] = idx

        self.assertIn("direct_caller", role_first_index)
        self.assertIn("indirect_dependent", role_first_index)
        # Semantic must never appear before structural signals
        if "semantic_related" in role_first_index:
            self.assertLess(role_first_index["direct_caller"],
                            role_first_index["semantic_related"])
            self.assertLess(role_first_index["indirect_dependent"],
                            role_first_index["semantic_related"])
        if "co_change" in role_first_index:
            self.assertLess(role_first_index["direct_caller"],
                            role_first_index["co_change"])
            self.assertLess(role_first_index["indirect_dependent"],
                            role_first_index["co_change"])
        if "co_change" in role_first_index and "semantic_related" in role_first_index:
            self.assertLess(role_first_index["co_change"],
                            role_first_index["semantic_related"])

    def test_semantic_fallback_filters_fixture_and_test_paths_for_source_targets(self):
        """FP guard: semantic fallback should help a source edit, not yank
        the agent into fixtures or tests that merely share tokens.
        """
        ctx = _build_ctx_with_fake_index(
            self.tmp.name,
            search_hits=[
                {"symbol_id": "fixture", "file": "evals/fixtures/demo_case.py",
                 "short_name": "greet_case", "score": 10.0},
                {"symbol_id": "test", "file": "tests/test_service.py",
                 "short_name": "test_greet", "score": 9.8},
                {"symbol_id": "source", "file": "app/greeter_helpers.py",
                 "short_name": "format_greeting", "score": 8.0},
            ],
        )
        patches = self._patch_structural(direct_callers=[], indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = self._run_get_context(ctx)

        semantic_files = {
            r["file"] for r in result["related_file_reasons"]
            if r.get("role") == "semantic_related"
        }
        self.assertIn("app/greeter_helpers.py", semantic_files)
        self.assertNotIn("evals/fixtures/demo_case.py", semantic_files)
        self.assertNotIn("tests/test_service.py", semantic_files)

    def test_framework_wiring_surfaces_route_file_as_probable_relation(self):
        """Laravel/Symfony-style route wiring is not a direct call edge but
        still matters for edits around controllers and their services. It
        must surface separately as a probable framework wiring relation.
        """
        with open(os.path.join(self.tmp.name, ".blindspot.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "language: php\n"
                "framework: laravel\n"
                "scan_dirs:\n"
                "  services: app/Services\n"
                "  controllers: app/Http/Controllers\n"
                "  routes: routes\n"
                "  tests: tests\n"
            )
        os.makedirs(os.path.join(self.tmp.name, "app", "Services"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "app", "Http", "Controllers"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "routes"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "app", "Services", "PricingService.php"), "w", encoding="utf-8") as f:
            f.write("<?php class PricingService { public function rateFor($tier) {} }\n")
        with open(os.path.join(self.tmp.name, "app", "Http", "Controllers", "CheckoutController.php"), "w", encoding="utf-8") as f:
            f.write("<?php class CheckoutController { public function quote() {} }\n")
        with open(os.path.join(self.tmp.name, "routes", "web.php"), "w", encoding="utf-8") as f:
            f.write("<?php Route::post('/checkout', [CheckoutController::class, 'quote']);\n")

        ctx = _build_ctx_with_fake_index(self.tmp.name)
        direct_callers = [
            {"file": "app/Http/Controllers/CheckoutController.php", "category": "controllers", "count": 1,
             "strongest_usage": "method_call", "usage_types": ["method_call"], "snippets": []},
        ]
        patches = self._patch_structural(direct_callers=direct_callers, indirect_dependents=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = ContextEngineService(ctx).get_context(
                target="app/Services/PricingService.php",
                intent="before_edit",
                symbol="rateFor",
                include_source=False,
            )

        route_reason = next(
            (item for item in result["related_file_reasons"] if item.get("file") == "routes/web.php"),
            None,
        )
        self.assertIsNotNone(route_reason, result["related_file_reasons"])
        self.assertEqual(route_reason["role"], "framework_entrypoint")
        self.assertEqual(route_reason["certainty"], "probable")
        self.assertEqual(route_reason["evidence_type"], "framework_wiring")
        self.assertIn("routes/web.php", result["relationship_buckets"]["probable"])


class CrossFileRefsFixtureRegressionTests(unittest.TestCase):
    """Ensure PHP and Java cross-file ``pending_calls`` still populate the
    ``refs`` table after semantic/co-change changes.

    FN guard: a regression here would silently reintroduce intra-file-only
    resolution for the two languages where it matters most for large
    backends (Laravel, Spring).
    """

    _REPO_ROOT = Path(__file__).resolve().parent.parent

    def _build_fixture(self, fixture_rel: str):
        src = self._REPO_ROOT / fixture_rel
        self.assertTrue(src.is_dir(), f"Missing fixture: {src}")
        tmp = Path(tempfile.mkdtemp(prefix="blindspot_fixtest_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dest = tmp / src.name
        shutil.copytree(src, dest)
        # Build the SQLite index in the copy so the source fixture stays clean.
        from blindspot.indexing.sqlite_index_manager import SQLiteIndexManager
        mgr = SQLiteIndexManager()
        mgr.set_project_path(str(dest))
        mgr.build_index(force_rebuild=True)
        return mgr

    def _load_ref_edges(self, mgr):
        with sqlite3.connect(mgr.index_path) as conn:
            rows = conn.execute(
                """
                SELECT sc.path AS caller_file, cl.path AS called_file,
                       s_caller.short_name AS caller_name,
                       s_called.short_name AS called_name
                FROM refs r
                JOIN symbols s_caller ON s_caller.symbol_id = r.caller_symbol_id
                JOIN symbols s_called ON s_called.symbol_id = r.called_symbol_id
                JOIN files sc ON sc.id = s_caller.file_id
                JOIN files cl ON cl.id = s_called.file_id
                """
            ).fetchall()
        return [dict(caller_file=r[0], called_file=r[1],
                     caller_name=r[2], called_name=r[3]) for r in rows]

    def test_php_laravel_fixture_populates_cross_file_refs(self):
        mgr = self._build_fixture("evals/fixtures/php_laravel_impact")
        edges = self._load_ref_edges(mgr)
        cross_file = [e for e in edges if e["caller_file"] != e["called_file"]]
        self.assertGreaterEqual(
            len(cross_file), 1,
            f"Expected \u22651 cross-file PHP ref, got {edges}",
        )
        # Controller should call into the Service (pricing / total / quote).
        controller_to_service = [
            e for e in cross_file
            if "Controller" in e["caller_file"] and "Service" in e["called_file"]
        ]
        self.assertTrue(
            controller_to_service,
            f"Laravel controller->service edge missing. Edges: {cross_file}",
        )

    def test_java_spring_fixture_populates_cross_file_refs(self):
        mgr = self._build_fixture("evals/fixtures/java_spring_impact")
        edges = self._load_ref_edges(mgr)
        cross_file = [e for e in edges if e["caller_file"] != e["called_file"]]
        self.assertGreaterEqual(
            len(cross_file), 1,
            f"Expected \u22651 cross-file Java ref, got {edges}",
        )
        pricing_called = [
            e for e in cross_file if e["called_name"] == "PricingService.rateFor"
            or (e["called_name"] == "rateFor" and "PricingService" in e["called_file"])
        ]
        self.assertTrue(
            pricing_called,
            f"Expected rateFor callee across files. Edges: {cross_file}",
        )

    def test_ts_nestjs_fixture_indexes_classes_and_di_edges(self):
        """FN guard: TS strategy previously missed ``type_identifier``
        class names and constructor-injection parameter types, which
        meant NestJS services never linked to their controllers. If that
        regresses, cross-file callers in modern JS/TS backends vanish.
        """
        mgr = self._build_fixture("evals/fixtures/ts_nestjs_impact")

        with sqlite3.connect(mgr.index_path) as conn:
            class_rows = conn.execute(
                "SELECT s.short_name, f.path "
                "FROM symbols s JOIN files f ON f.id=s.file_id "
                "WHERE s.type='class'"
            ).fetchall()
        class_names = {row[0] for row in class_rows}
        self.assertIn("UsersController", class_names)
        self.assertIn("UsersService", class_names)
        self.assertIn("PrismaService", class_names)

        edges = self._load_ref_edges(mgr)
        di_edge = [
            e for e in edges
            if e["caller_name"] == "UsersController.constructor"
            and e["called_name"] == "UsersService"
        ]
        self.assertTrue(
            di_edge,
            f"Missing DI edge UsersController -> UsersService. Edges: {edges}",
        )
        cross_file_di = [e for e in di_edge if e["caller_file"] != e["called_file"]]
        self.assertTrue(
            cross_file_di,
            f"DI edge must cross file boundaries. Edges: {di_edge}",
        )

    def test_ts_nestjs_intent_query_surfaces_service_method(self):
        """FP guard for BM25 owner-token flooding. With the NestJS
        fixture indexed, a capability-style query (``find user by id``)
        must resolve to ``UsersService.findById`` rather than any
        incidental helper/class whose file path happens to share
        tokens with the query. Catches regressions in the intent
        reranker (type factor, method-name-match bonus).
        """
        mgr = self._build_fixture("evals/fixtures/ts_nestjs_impact")
        hits = mgr.search_symbols("find user by id", limit=5)
        self.assertTrue(hits, "BM25 returned no hits for a trivial query")
        top = hits[0]
        self.assertEqual(
            top.get("short_name"), "UsersService.findById",
            f"Expected UsersService.findById as top-1. Got: "
            f"{[(h.get('short_name'), h.get('score')) for h in hits]}",
        )
        self.assertEqual(top.get("type"), "method")

    def test_ts_owner_only_match_loses_to_method_name_match(self):
        """FP guard: when two candidates tie on owner tokens, the one
        whose method name also matches the query must win. This is the
        concrete BetweenUs failure mode: methods of
        ``ProviderTokenVerifierService`` flooding top-K on any
        ``provider token`` query regardless of what the method does.
        """
        mgr = self._build_fixture("evals/fixtures/ts_nestjs_impact")
        # ``find`` only matches the method name of ``UsersService.findById``
        # — other symbols in the fixture share no tokens. Method-name
        # match bonus should keep it at the top.
        hits = mgr.search_symbols("find", limit=5)
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top.get("short_name"), "UsersService.findById")

    def test_php_procedural_fixture_captures_top_level_and_typed_calls(self):
        """FN guard for three PHP call patterns that historically lost
        cross-file attribution:

        1. Top-level script calls (``SessionService::getInstance()`` in
           ``bootstrap.php``) — captured via the synthetic
           ``__file_scope__`` caller.
        2. Local-variable member calls (``$session->refresh(42)``) —
           receiver type recovered from a preceding ``$session =
           SessionService::getInstance()`` assignment.
        3. Constructor-promoted DI (``$this->payments->charge(...)``)
           — receiver type recovered from the promoted parameter on
           ``__construct``.

        FP note: the synthetic ``__file_scope__`` caller is filtered
        from BM25 ingestion and from the ``related_file_reasons``
        generic-hook list, so it does not leak into user-visible
        results.
        """
        mgr = self._build_fixture("evals/fixtures/php_procedural_impact")
        edges = self._load_ref_edges(mgr)

        def edge_present(caller: str, called: str) -> bool:
            return any(
                e["caller_name"] == caller and e["called_name"] == called
                for e in edges
            )

        self.assertTrue(
            edge_present("__file_scope__", "SessionService.getInstance"),
            f"Missing top-level scoped-call edge. Edges: {edges}",
        )
        self.assertTrue(
            edge_present("__file_scope__", "SessionService.refresh"),
            f"Missing local-variable typed member-call edge. Edges: {edges}",
        )
        self.assertTrue(
            edge_present("CheckoutController.pay", "PaymentService.charge"),
            f"Missing constructor-promoted DI edge. Edges: {edges}",
        )
        # Sibling method caller must also be captured — fixture asserts
        # the mixed-case direct_callers ordering below.
        self.assertTrue(
            edge_present("SessionWorker.process", "SessionService.refresh"),
            f"Missing in-method caller edge. Edges: {edges}",
        )

        # __file_scope__ must not leak into BM25 search results.
        for query in ("session", "refresh", "payment"):
            for hit in mgr.search_symbols(query, limit=10):
                self.assertNotEqual(
                    hit.get("short_name"), "__file_scope__",
                    f"Synthetic file-scope symbol leaked into search "
                    f"for query={query!r}",
                )

    def test_php_file_scope_caller_ranks_below_method_caller(self):
        """FP guard for procedural PHP noise in ``direct_callers``.

        ``SessionService.refresh`` is called from both:
            * ``SessionWorker.process`` (real in-method method_call)
            * ``bootstrap.php``         (synthetic file-scope call)

        After normalization the in-method caller must rank first and
        the file-scope caller must surface with its dedicated
        ``module_script`` usage tag — never as a peer ``method_call``.
        This preserves the procedural-entry-point signal while
        eliminating the noise that previously buried real callers on
        Laravel/WordPress-style repos.
        """
        import shutil
        import tempfile

        from blindspot.adapters.project_structure import get_project_structure
        from blindspot.adapters.symbol_resolver import SymbolResolver
        from blindspot.indexing.sqlite_index_manager import SQLiteIndexManager

        src = Path("evals/fixtures/php_procedural_impact").resolve()
        tmp = Path(tempfile.mkdtemp(prefix="blindspot_file_scope_"))
        try:
            dest = tmp / src.name
            shutil.copytree(src, dest)
            mgr = SQLiteIndexManager()
            mgr.set_project_path(str(dest))
            mgr.build_index(force_rebuild=True)
            structure = get_project_structure(str(dest))
            resolver = SymbolResolver(str(dest), structure, index_manager=mgr)

            result = resolver.get_symbol_change_context(
                rel_path="app/Services/SessionService.php",
                symbol="refresh",
                change_type="modify",
            )
            direct_callers = result.get("direct_callers") or []

            self.assertGreaterEqual(
                len(direct_callers), 2,
                f"Expected both method_call and module_script callers, "
                f"got {direct_callers}",
            )
            first = direct_callers[0]
            second = direct_callers[1]
            self.assertEqual(
                first.get("strongest_usage"), "method_call",
                f"Real in-method caller must rank first. Got {direct_callers}",
            )
            self.assertEqual(
                second.get("strongest_usage"), "module_script",
                f"File-scope caller must rank below method_call. Got {direct_callers}",
            )
            self.assertIn(
                "SessionWorker",
                str(first.get("file", "")),
                f"Top caller should be SessionWorker.process host file. Got {first}",
            )
            self.assertEqual(
                second.get("file"), "bootstrap.php",
                f"module_script caller should be bootstrap.php. Got {second}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_php_legacy_di_resolves_cross_file_service_calls(self):
        """FN guard for two non-promoted PHP DI patterns whose receiver
        types previously could not be recovered:

        1. Traditional constructor-body assignment
           (``public function __construct(Foo $x) { $this->x = $x; }``
           → ``$this->x->bar()``).
        2. Typed class property only
           (``private Foo $x;`` with no matching ctor assignment
           → ``$this->x->bar()``).

        Both must emit ``Service.method`` cross-file edges so Laravel
        and Symfony projects that predate PHP 8.0 constructor promotion
        still get accurate ``direct_callers`` resolution.

        FP guard: opaque RHS in the ctor body (factories, ternaries) is
        intentionally ignored; the unit tests in the PHP strategy cover
        that path.
        """
        mgr = self._build_fixture("evals/fixtures/php_legacy_di_impact")
        edges = self._load_ref_edges(mgr)

        def edge_present(caller: str, called: str) -> bool:
            return any(
                e["caller_name"] == caller and e["called_name"] == called
                for e in edges
            )

        self.assertTrue(
            edge_present("LegacyCtorController.pay", "PaymentService.charge"),
            f"Ctor-body DI did not resolve PaymentService.charge. "
            f"Edges: {edges}",
        )
        self.assertTrue(
            edge_present("TypedPropController.log", "AuditService.record"),
            f"Typed class property alone did not resolve AuditService.record. "
            f"Edges: {edges}",
        )

    def test_ts_variable_bound_calls_resolve_to_owning_class(self):
        """FN guard for four TS/NestJS receiver patterns that
        historically dropped their ``direct_caller`` attribution:

        1. Constructor-promoted DI
           (``constructor(private readonly payments: PaymentService)``
           → ``this.payments.charge(...)``).
        2. Explicit class field
           (``private notifications: NotificationService = new ...``
           → ``this.notifications.notify(...)``).
        3. Local ``new`` expression
           (``const extra = new PaymentService(); extra.charge(...)``).
        4. Explicit local type annotation
           (``const bonus: PaymentService = ...; bonus.charge(...)``).

        Each pattern must land a cross-file edge to the *owning class's*
        method in the refs table. Before this change, calls on the bare
        receiver name (``extra`` / ``bonus`` / ``this.payments``)
        resolved only to the unqualified method-name fallback and
        silently lost their file attribution when multiple classes
        shared a method name.
        """
        mgr = self._build_fixture("evals/fixtures/ts_variable_bound_calls")
        edges = self._load_ref_edges(mgr)

        def edge_present(caller: str, called: str) -> bool:
            return any(
                e["caller_name"] == caller and e["called_name"] == called
                for e in edges
            )

        # Patterns 1, 3, 4 all call ``PaymentService.charge`` — the
        # refs table dedupes per (caller, called) pair, so one edge
        # covers all three receivers.
        self.assertTrue(
            edge_present("OrdersController.placeOrder", "PaymentService.charge"),
            "Missing PaymentService.charge edge from constructor-promoted "
            f"DI / new-expr / annotated local. Edges: {edges}",
        )

        # Pattern 2: typed class field resolved through this.<prop>.
        self.assertTrue(
            edge_present(
                "OrdersController.placeOrder", "NotificationService.notify"
            ),
            "Missing NotificationService.notify edge from typed field "
            f"declaration. Edges: {edges}",
        )

    def test_ts_constructor_body_di_resolves_classic_and_mixed(self):
        """Legacy Angular / older Nest style DI where constructor params
        are NOT promoted. Three receiver shapes must all land
        class-qualified edges, and one opaque-RHS shape must stay
        unresolved so phantom property types never leak:

        1. Classic body assignment ``this.users = users`` where the
           RHS is a typed constructor parameter.
        2. Body instantiation ``this.audit = new AuditService()`` where
           the RHS is a ``new`` expression.
        3. Mixed style: one promoted param alongside one non-promoted
           param assigned in the body; both must resolve inside the same
           controller.
        4. FP guard: ``this.orphan = opaque`` where ``opaque: any`` has
           no user-defined type. A later ``this.orphan.get(...)`` must
           NOT produce an edge to any ``.get`` method.
        """
        mgr = self._build_fixture("evals/fixtures/ts_constructor_body_di")
        edges = self._load_ref_edges(mgr)

        def edge_present(caller: str, called: str) -> bool:
            return any(
                e["caller_name"] == caller and e["called_name"] == called
                for e in edges
            )

        # Classic: param-to-field body assignment resolves the owner.
        self.assertTrue(
            edge_present("ClassicUsersController.getOne", "UsersService.findById"),
            f"Missing UsersService.findById edge from classic param body "
            f"assignment. Edges: {edges}",
        )
        self.assertTrue(
            edge_present("ClassicUsersController.getOne", "Logger.info"),
            f"Missing Logger.info edge from classic param body assignment. "
            f"Edges: {edges}",
        )
        # Body instantiation with ``new`` resolves via _resolve_new_class_name.
        self.assertTrue(
            edge_present("ClassicUsersController.getOne", "AuditService.record"),
            f"Missing AuditService.record edge from ``this.audit = new ...`` "
            f"body assignment. Edges: {edges}",
        )

        # Mixed: promoted + body assignments must coexist on one ctor.
        self.assertTrue(
            edge_present("MixedUsersController.getOne", "UsersService.findById"),
            "Missing UsersService.findById edge from promoted param in "
            f"mixed controller. Edges: {edges}",
        )
        self.assertTrue(
            edge_present("MixedUsersController.getOne", "Logger.info"),
            "Missing Logger.info edge from non-promoted body assignment in "
            f"mixed controller. Edges: {edges}",
        )
        self.assertTrue(
            edge_present("MixedUsersController.getOne", "AuditService.record"),
            "Missing AuditService.record edge from ``new`` body assignment "
            f"in mixed controller. Edges: {edges}",
        )

        # FP guard: opaque RHS must not introduce a phantom property
        # type. ``this.orphan`` is never declared as a field and
        # ``this.orphan = opaque`` where ``opaque: any`` carries no
        # user-defined type, so the call site must stay unresolved.
        orphan_edges = [
            e for e in edges
            if e["caller_name"] == "ClassicUsersController.getOne"
            and e["called_name"].endswith(".get")
        ]
        self.assertEqual(
            orphan_edges, [],
            f"FP guard breach: opaque RHS produced a phantom ``.get`` edge. "
            f"Offending edges: {orphan_edges}",
        )

    def test_ts_non_ascii_symbols_preserved(self):
        """Doğruluk guard: tree-sitter indexes BYTES, so any TS source
        containing multi-byte characters (Turkish letters, em-dashes,
        smart quotes, bullets) previously corrupted every symbol
        declared after the first non-ASCII character. The strategy now
        slices the UTF-8 buffer directly via ``TraversalContext.text``,
        which must preserve exact symbol names and undamaged signatures.

        The fixture deliberately stacks heavy non-ASCII content in
        comments, class names, method names, parameter names and string
        literals. A regression here would manifest as mangled short_names
        like ``iparisController`` or truncated signatures like
        ``rocess(amount: number)`` which silently break every downstream
        consumer (refs lookup, direct_callers, get_symbol_body).
        """
        mgr = self._build_fixture("evals/fixtures/ts_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            rows = conn.execute(
                """
                SELECT s.short_name, s.type, s.signature
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                """
            ).fetchall()

        symbols = {r[0]: {"type": r[1], "signature": r[2]} for r in rows}

        # Class names must be byte-exact. Turkish capital letters and
        # diacritics commonly occupy the first byte position in
        # identifiers, so any off-by-N slicing shows up immediately.
        for expected_class in ("ÖdemeService", "Günlükçü", "SiparişController"):
            self.assertIn(
                expected_class, symbols,
                f"Class {expected_class!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )
            self.assertEqual(symbols[expected_class]["type"], "class")

        # Method names must be byte-exact AND qualified with the
        # byte-exact class name.
        for expected_method in (
            "ÖdemeService.pay",
            "Günlükçü.info",
            "SiparişController.process",
            "SiparişController.constructor",
        ):
            self.assertIn(
                expected_method, symbols,
                f"Method {expected_method!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )

        # Signatures must survive intact — a mid-signature cut from
        # byte-drift would silently truncate method arity.
        self.assertEqual(
            symbols["SiparişController.process"]["signature"],
            "async process(amount: number): Promise<string> {",
        )
        self.assertEqual(
            symbols["ÖdemeService.pay"]["signature"],
            "pay(amount: number): Promise<string> {",
        )

        # Cross-file DI edges must land on the byte-exact owner class
        # names so refs resolution doesn't fall back to bare method
        # names (which would silently change blast_radius output).
        edges = self._load_ref_edges(mgr)

        def edge(caller: str, called: str) -> bool:
            return any(
                e["caller_name"] == caller and e["called_name"] == called
                for e in edges
            )

        self.assertTrue(
            edge("SiparişController.process", "ÖdemeService.pay"),
            f"Missing byte-exact cross-file edge from Turkish controller "
            f"to Turkish service. Edges: {edges}",
        )
        self.assertTrue(
            edge("SiparişController.process", "Günlükçü.info"),
            f"Missing byte-exact cross-file edge to ``Günlükçü.info``. "
            f"Edges: {edges}",
        )
    def test_java_non_ascii_symbols_preserved(self):
        """Doğruluk guard (Java): identical contract to the TS non-ASCII
        test. Java strategy now slices the UTF-8 buffer via
        ``TraversalContext.text``/``_slice`` instead of indexing the
        decoded ``str`` with tree-sitter byte offsets. The fixture
        stacks Turkish identifiers in class names, method names,
        parameters, comments and the package path so any residual
        str-slice drift surfaces as a mangled ``short_name``, a
        truncated signature, a corrupted import entry, or a cross-file
        edge landing on the wrong owner.
        """
        mgr = self._build_fixture("evals/fixtures/java_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            sym_rows = conn.execute(
                "SELECT short_name, type, signature FROM symbols"
            ).fetchall()
            import_rows = conn.execute(
                "SELECT path, imports FROM files"
            ).fetchall()

        symbols = {r[0]: {"type": r[1], "signature": r[2]} for r in sym_rows}

        for expected_class in ("ÖdemeService", "SiparişController"):
            self.assertIn(
                expected_class, symbols,
                f"Class {expected_class!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )
            self.assertEqual(symbols[expected_class]["type"], "class")

        for expected_method in (
            "ÖdemeService.ödemeAl",
            "SiparişController.işle",
        ):
            self.assertIn(
                expected_method, symbols,
                f"Method {expected_method!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )

        self.assertEqual(
            symbols["ÖdemeService.ödemeAl"]["signature"],
            "public String ödemeAl(int tutar) {",
        )
        self.assertEqual(
            symbols["SiparişController.işle"]["signature"],
            "public String işle(int tutar) {",
        )

        # Import paths must round-trip through byte slicing without
        # losing the non-ASCII package segment; otherwise cross-file
        # resolvers that key on import paths silently miss owners.
        controller_imports = next(
            json.loads(imports)
            for path, imports in import_rows
            if path.endswith("SiparişController.java")
        )
        self.assertIn("com.örnek.service.ÖdemeService", controller_imports)

        # Cross-file refs must land on the byte-exact Turkish owner.
        edges = self._load_ref_edges(mgr)
        cross_file = [
            e for e in edges
            if e["caller_name"] == "SiparişController.işle"
            and e["called_name"] == "ÖdemeService.ödemeAl"
            and e["caller_file"] != e["called_file"]
        ]
        self.assertTrue(
            cross_file,
            "Missing byte-exact cross-file Java edge "
            "SiparişController.işle -> ÖdemeService.ödemeAl. "
            f"Edges: {edges}",
        )


    def test_js_non_ascii_symbols_preserved(self):
        """Doğruluk guard (JavaScript): the JS strategy now routes every
        identifier/signature extraction through byte-sliced ``_get_node_text``
        and ``_get_js_function_signature`` instead of indexing the decoded
        ``str`` with tree-sitter byte offsets. The fixture stacks Turkish
        identifiers in class names, method names, parameters, constructor
        bodies and heavy non-ASCII comments (em-dash, smart quotes, bullets).
        A regression surfaces immediately as a mangled ``short_name`` or a
        truncated signature — either of which silently corrupts every
        downstream consumer that keys off symbol identity.
        """
        mgr = self._build_fixture("evals/fixtures/js_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            sym_rows = conn.execute(
                "SELECT short_name, type, signature FROM symbols"
            ).fetchall()

        symbols = {r[0]: {"type": r[1], "signature": r[2]} for r in sym_rows}

        for expected_class in ("ÖdemeService", "Günlükçü", "SiparişController"):
            self.assertIn(
                expected_class, symbols,
                f"Class {expected_class!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )
            self.assertEqual(symbols[expected_class]["type"], "class")

        for expected_method in (
            "ÖdemeService.ödemeAl",
            "Günlükçü.info",
            "SiparişController.işle",
            "SiparişController.constructor",
        ):
            self.assertIn(
                expected_method, symbols,
                f"Method {expected_method!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )

        self.assertEqual(
            symbols["ÖdemeService.ödemeAl"]["signature"],
            "ödemeAl(tutar) {",
        )
        self.assertEqual(
            symbols["Günlükçü.info"]["signature"],
            "info(mesaj) {",
        )
        self.assertEqual(
            symbols["SiparişController.işle"]["signature"],
            "işle(tutar) {",
        )

    def test_kotlin_non_ascii_symbols_preserved(self):
        """Doğruluk guard (Kotlin): two silent-corruption surfaces
        converge here. (1) ``_get_kotlin_function_signature`` /
        function-header fallback previously sliced ``context.content``
        (str) with tree-sitter byte offsets, so non-ASCII files before
        the target node dragged the window off by N bytes. (2)
        ``_clean_identifier`` and the package regex used ASCII-only
        character classes, truncating Unicode identifiers at the first
        non-ASCII byte (``Günlükçü`` → ``G``). The fixture exercises
        both: non-ASCII class/function names, non-ASCII package path,
        non-ASCII comments and a non-ASCII field type reference that
        must resolve to the byte-exact owner class.
        """
        mgr = self._build_fixture("evals/fixtures/kotlin_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            sym_rows = conn.execute(
                "SELECT short_name, type, signature FROM symbols"
            ).fetchall()
            file_rows = conn.execute(
                "SELECT path, package, imports FROM files"
            ).fetchall()

        symbols = {r[0]: {"type": r[1], "signature": r[2]} for r in sym_rows}

        for expected_class in ("ÖdemeService", "Günlükçü", "SiparişController"):
            self.assertIn(
                expected_class, symbols,
                f"Class {expected_class!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )
            self.assertEqual(symbols[expected_class]["type"], "class")

        for expected_method in (
            "ÖdemeService.ödemeAl",
            "Günlükçü.info",
            "SiparişController.işle",
        ):
            self.assertIn(
                expected_method, symbols,
                f"Method {expected_method!r} missing or mangled. "
                f"Indexed short_names: {sorted(symbols.keys())}",
            )

        self.assertEqual(
            symbols["ÖdemeService.ödemeAl"]["signature"],
            "fun ödemeAl(tutar: Int): String {",
        )
        self.assertEqual(
            symbols["SiparişController.işle"]["signature"],
            "fun işle(tutar: Int): String {",
        )

        # Package + imports must round-trip non-ASCII segments. The
        # package regex and the fallback import extractor both
        # previously whitelisted ASCII-only identifier chars.
        packages = {Path(r[0]).name: r[1] for r in file_rows}
        self.assertEqual(
            packages.get("SiparişController.kt"),
            "com.örnek.controller",
            f"Package mangled. Got: {packages}",
        )
        imports = next(
            json.loads(imp)
            for path, _pkg, imp in file_rows
            if path.endswith("SiparişController.kt")
        )
        self.assertIn("com.örnek.service.ÖdemeService", imports)
        self.assertIn("com.örnek.service.Günlükçü", imports)

        # Cross-file edges must land on the byte-exact Turkish owner.
        edges = self._load_ref_edges(mgr)
        self.assertTrue(
            any(
                e["caller_name"] == "SiparişController.işle"
                and e["called_name"] == "ÖdemeService.ödemeAl"
                and e["caller_file"] != e["called_file"]
                for e in edges
            ),
            f"Missing byte-exact cross-file Kotlin edge "
            f"SiparişController.işle -> ÖdemeService.ödemeAl. Edges: {edges}",
        )
        self.assertTrue(
            any(
                e["caller_name"] == "SiparişController.işle"
                and e["called_name"] == "Günlükçü.info"
                for e in edges
            ),
            f"Missing byte-exact Kotlin edge to Günlükçü.info. "
            f"Edges: {edges}",
        )

    def test_zig_non_ascii_symbols_and_line_numbers_preserved(self):
        """Doğruluk guard (Zig): Zig routes through
        ``base_strategy._extract_line_number`` and ``_safe_extract_text``,
        which previously indexed the decoded ``str`` with tree-sitter
        byte offsets. Any multi-byte content before a declaration
        shifted the newline count, so reported line numbers drifted past
        the real one — silently breaking ``get_symbol_body``, jump-to
        and every downstream consumer that keys off ``SymbolInfo.line``.
        This fixture packs heavy non-ASCII bytes (em-dash, smart quotes,
        bullets, Turkish letters) in the comment block before every
        declaration so any residual str-slice bug surfaces as an
        off-by-N line number or a mangled symbol name.
        """
        mgr = self._build_fixture("evals/fixtures/zig_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            sym_rows = conn.execute(
                "SELECT short_name, type, line, signature FROM symbols"
            ).fetchall()

        by_name = {r[0]: {"type": r[1], "line": r[2], "signature": r[3]} for r in sym_rows}

        for expected in ("pay", "info", "process_order"):
            self.assertIn(
                expected, by_name,
                f"Zig symbol {expected!r} missing or mangled. "
                f"Indexed short_names: {sorted(by_name.keys())}",
            )

        # Line numbers must match the source exactly. The fixture's
        # non-ASCII comment block sits between lines 1-5 and 23-25; if
        # ``_extract_line_number`` counted newlines in ``str[:byte_pos]``
        # the recorded line would overshoot by several lines.
        self.assertEqual(by_name["pay"]["line"], 10)
        self.assertEqual(by_name["info"]["line"], 18)
        self.assertEqual(by_name["process_order"]["line"], 26)

        # Signatures start with the declaration keyword — byte drift
        # would produce signatures beginning mid-keyword like ``ub fn``.
        self.assertTrue(
            by_name["pay"]["signature"].startswith("pub fn pay("),
            f"Zig signature mangled: {by_name['pay']['signature']!r}",
        )
        self.assertTrue(
            by_name["process_order"]["signature"].startswith(
                "pub fn process_order("
            ),
            f"Zig signature mangled: {by_name['process_order']['signature']!r}",
        )

    def test_python_non_ascii_symbols_preserved(self):
        """Doğruluk guard (Python): locks in the audit finding that
        ``python_strategy.py`` is byte-safe by construction — it uses
        the stdlib ``ast`` module (not tree-sitter), so every symbol
        name comes from already-decoded ``node.name`` / ``arg.arg``
        attributes and every line number from CPython's native
        ``lineno`` / ``end_lineno``. There are no byte offsets, no
        content slicing, no ``get_source_segment`` calls. This test
        exists to catch a silent regression if a future refactor
        switches to tree-sitter-python or starts using byte-indexed
        source segments — either of which would reintroduce the same
        class of silent corruption fixed in the Java/TS/Kotlin/Zig
        strategies.
        """
        mgr = self._build_fixture("evals/fixtures/python_non_ascii")

        with sqlite3.connect(mgr.index_path) as conn:
            sym_rows = conn.execute(
                "SELECT short_name, type, line, signature FROM symbols"
            ).fetchall()
            file_rows = conn.execute(
                "SELECT path, imports FROM files"
            ).fetchall()

        by_name = {
            r[0]: {"type": r[1], "line": r[2], "signature": r[3]}
            for r in sym_rows
        }

        for expected_class in ("ÖdemeService", "Günlükçü", "SiparişController"):
            self.assertIn(
                expected_class, by_name,
                f"Class {expected_class!r} missing or mangled. "
                f"Indexed short_names: {sorted(by_name.keys())}",
            )
            self.assertEqual(by_name[expected_class]["type"], "class")

        for expected_method in (
            "ÖdemeService.ödemeAl",
            "Günlükçü.info",
            "SiparişController.işle",
            "SiparişController.__init__",
        ):
            self.assertIn(
                expected_method, by_name,
                f"Method {expected_method!r} missing or mangled. "
                f"Indexed short_names: {sorted(by_name.keys())}",
            )

        # Top-level function with non-ASCII name must also land.
        self.assertIn("process_sipariş", by_name)
        self.assertEqual(by_name["process_sipariş"]["type"], "function")

        # Line numbers must be byte-exact. Non-ASCII comment bytes
        # before each declaration would otherwise shift a str-slice-
        # based counter.
        self.assertEqual(by_name["ÖdemeService"]["line"], 7)
        self.assertEqual(by_name["ÖdemeService.ödemeAl"]["line"], 10)
        self.assertEqual(by_name["SiparişController.işle"]["line"], 13)
        self.assertEqual(by_name["process_sipariş"]["line"], 22)

        # Signatures must preserve non-ASCII parameter and function
        # names intact. The Python strategy builds signatures from
        # ``arg.arg`` rather than slicing source text, so a regression
        # would most likely come from a future strategy swap.
        self.assertEqual(
            by_name["ÖdemeService.ödemeAl"]["signature"],
            "def ödemeAl(self, tutar):",
        )
        self.assertEqual(
            by_name["SiparişController.işle"]["signature"],
            "def işle(self, tutar):",
        )
        self.assertEqual(
            by_name["process_sipariş"]["signature"],
            "def process_sipariş(tutar):",
        )

        # Import paths must round-trip non-ASCII module + symbol segments.
        imports = next(
            json.loads(imp)
            for path, imp in file_rows
            if path.endswith("sipariş.py")
        )
        self.assertIn("src.ödeme.ÖdemeService", imports)
        self.assertIn("src.ödeme.Günlükçü", imports)

    def test_js_constructor_body_di_resolves_cross_file_service_calls(self):
        """JS DI tracking guard: ``const { X } = require('./mod')``
        brings a class into scope; ``this.svc = new X()`` in the
        constructor registers the instance-field type; a subsequent
        ``this.svc.foo()`` call inside another method must resolve to
        ``X.foo`` on the refs table, producing a cross-file
        direct-caller edge.

        Pre-fix behaviour was a silent FN: the controller-to-service
        edge was never recorded because JS had no instance-field
        tracking. This test locks in the fix ported from the TS
        strategy so Node.js service-heavy projects keep producing
        direct_callers edges through classic constructor DI.
        """
        mgr = self._build_fixture("evals/fixtures/js_non_ascii")
        edges = self._load_ref_edges(mgr)

        cross_file = [
            e for e in edges
            if e["caller_file"] != e["called_file"]
        ]
        self.assertGreaterEqual(
            len(cross_file), 2,
            f"Expected ≥2 cross-file JS DI edges, got {edges}",
        )

        by_target = {
            (e["caller_name"], e["called_name"])
            for e in edges
        }
        self.assertIn(
            ("SiparişController.işle", "ÖdemeService.ödemeAl"),
            by_target,
            f"Missing this.ödeme.ödemeAl() edge. All edges: {edges}",
        )
        self.assertIn(
            ("SiparişController.işle", "Günlükçü.info"),
            by_target,
            f"Missing this.günlük.info() edge. All edges: {edges}",
        )
