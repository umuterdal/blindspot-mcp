"""Small evaluation harness for Blindspot context quality."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blindspot.services.context_engine_service import ContextEngineService
from blindspot.services.index_management_service import IndexManagementService
from blindspot.services.project_management_service import ProjectManagementService


@dataclass
class _FakeLifespan:
    base_path: str = ""
    settings: Any = None
    file_count: int = 0
    index_manager: Any = None


class _FakeReqCtx:
    def __init__(self) -> None:
        self.lifespan_context = _FakeLifespan()


class _FakeCtx:
    def __init__(self) -> None:
        self.request_context = _FakeReqCtx()


FIXTURES = [
    {
        "name": "python_indirect_test",
        "path": "evals/fixtures/python_indirect_test",
        "request": {
            "target": "app/orders.py",
            "intent": "before_edit",
            "symbol": "total_for",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"app/api.py"},
            "indirect_dependents": {"tests/test_orders.py"},
            "related_files": {"app/orders.py", "app/api.py", "tests/test_orders.py"},
        },
    },
    {
        "name": "javascript_express_impact",
        "path": "evals/fixtures/javascript_express_impact",
        "request": {
            "target": "src/services/pricing.js",
            "intent": "before_edit",
            "symbol": "rateFor",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"src/controllers/api.js"},
            "indirect_dependents": {"tests/api.test.js"},
            "related_files": {
                "src/services/pricing.js",
                "src/controllers/api.js",
                "tests/api.test.js",
            },
        },
    },
    {
        "name": "go_indirect_test",
        "path": "evals/fixtures/go_indirect_test",
        "request": {
            "target": "internal/pricing/service.go",
            "intent": "before_edit",
            "symbol": "RateFor",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"internal/api/handler.go"},
            "indirect_dependents": {"internal/api/handler_test.go"},
            "related_files": {
                "internal/pricing/service.go",
                "internal/api/handler.go",
                "internal/api/handler_test.go",
            },
        },
    },
    {
        "name": "dart_flutter_impact",
        "path": "evals/fixtures/dart_flutter_impact",
        "request": {
            "target": "lib/services/pricing_service.dart",
            "intent": "before_edit",
            "symbol": "rateFor",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"lib/screens/checkout_screen.dart"},
            "indirect_dependents": {"test/checkout_screen_test.dart"},
            "related_files": {
                "lib/services/pricing_service.dart",
                "lib/screens/checkout_screen.dart",
                "test/checkout_screen_test.dart",
            },
        },
    },
    {
        "name": "php_laravel_impact",
        "path": "evals/fixtures/php_laravel_impact",
        "request": {
            "target": "app/Services/PricingService.php",
            "intent": "before_edit",
            "symbol": "rateFor",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"app/Http/Controllers/CheckoutController.php"},
            "indirect_dependents": {"tests/Feature/CheckoutControllerTest.php"},
            "related_files": {
                "app/Services/PricingService.php",
                "app/Http/Controllers/CheckoutController.php",
                "tests/Feature/CheckoutControllerTest.php",
            },
            "related_file_roles": {
                "routes/web.php": "framework_entrypoint",
                "bootstrap/app.php": "framework_entrypoint",
            },
        },
    },
    {
        "name": "java_spring_impact",
        "path": "evals/fixtures/java_spring_impact",
        "request": {
            "target": "src/main/java/com/example/service/PricingService.java",
            "intent": "before_edit",
            "symbol": "rateFor",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect": {
            "direct_callers": {"src/main/java/com/example/controller/CheckoutController.java"},
            "related_files": {
                "src/main/java/com/example/service/PricingService.java",
                "src/main/java/com/example/controller/CheckoutController.java",
            },
        },
    },
    {
        # Exercises owner-aware lookup: two classes define a `save` method
        # each. Asking about User.save should not pull in Order callers.
        "name": "python_ambiguous_method",
        "path": "evals/fixtures/python_ambiguous_method",
        "request": {
            "target": "app/models.py",
            "intent": "before_edit",
            "symbol": "save",
            "owner": "User",
            "include_source": False,
            "change_type": "signature_change",
        },
        "expect_exact": {
            "direct_callers": {"app/user_service.py"},
        },
        "expect_not_in": {
            "direct_callers": {"app/order_service.py"},
        },
        "expect": {
            "related_files": {"app/models.py", "app/user_service.py"},
        },
    },
]


def _evaluate_fixture(root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    fixture_path = root / fixture["path"]
    ctx = _FakeCtx()
    ProjectManagementService(ctx).initialize_project(str(fixture_path))
    IndexManagementService(ctx).rebuild_deep_index()
    started = time.perf_counter()
    result = ContextEngineService(ctx).get_context(**fixture["request"])
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    direct_files = {item.get("file") for item in result.get("direct_callers", [])}
    indirect_files = {item.get("file") for item in result.get("indirect_dependents", [])}
    related_files = set(result.get("related_files", []))

    expected = fixture.get("expect", {})
    expected_exact = fixture.get("expect_exact", {})
    expected_not_in = fixture.get("expect_not_in", {})
    checks: dict[str, bool] = {
        "confidence_high_enough": result.get("confidence") in {"high", "medium"},
        "has_edit_plan": bool(result.get("edit_plan", {}).get("steps")),
    }

    if "direct_callers" in expected:
        checks["direct_callers"] = expected["direct_callers"].issubset(direct_files)
    if "indirect_dependents" in expected:
        checks["indirect_dependents"] = expected["indirect_dependents"].issubset(indirect_files)
    if "related_files" in expected:
        checks["related_files"] = expected["related_files"].issubset(related_files)
    if "related_file_roles" in expected:
        produced_roles = {
            item.get("file"): item.get("role")
            for item in result.get("related_file_reasons", [])
        }
        checks["related_file_roles"] = all(
            produced_roles.get(path) == role
            for path, role in expected["related_file_roles"].items()
        )

    # Exact-set precision: the produced set must equal the expected set.
    for key, expected_set in expected_exact.items():
        produced = direct_files if key == "direct_callers" else indirect_files if key == "indirect_dependents" else related_files
        checks[f"{key}_exact"] = produced == expected_set

    # False-positive bound: the forbidden files must not appear.
    for key, forbidden in expected_not_in.items():
        produced = direct_files if key == "direct_callers" else indirect_files if key == "indirect_dependents" else related_files
        checks[f"{key}_no_false_positives"] = forbidden.isdisjoint(produced)

    return {
        "fixture": fixture["name"],
        "passed": all(checks.values()),
        "checks": checks,
        "elapsed_ms": elapsed_ms,
        "summary": {
            "confidence": result.get("confidence"),
            "blast_radius": result.get("blast_radius"),
            "related_files": result.get("related_files"),
            "related_file_reasons": result.get("related_file_reasons"),
            "direct_callers": sorted(direct_files),
            "indirect_dependents": sorted(indirect_files),
        },
    }


def _evaluate_synthetic_large_repo(file_count: int = 500, latency_ms_budget: int = 4000) -> dict[str, Any]:
    """Build a large synthetic Python repo and measure latency on get_context."""
    tmp_root = Path(tempfile.mkdtemp(prefix="blindspot_eval_large_"))
    try:
        app_dir = tmp_root / "app"
        callers_dir = tmp_root / "callers"
        app_dir.mkdir()
        callers_dir.mkdir()

        (app_dir / "core.py").write_text(
            "def target_function(x):\n"
            "    return x * 2\n",
            encoding="utf-8",
        )
        # 5 files call target_function; the rest are noise.
        caller_files = []
        for index in range(5):
            name = f"caller_{index}.py"
            caller_files.append(f"callers/{name}")
            (callers_dir / name).write_text(
                "from app.core import target_function\n\n"
                f"def do_{index}():\n"
                f"    return target_function({index})\n",
                encoding="utf-8",
            )
        # Fill the rest with unrelated files.
        for index in range(file_count - (1 + 5)):
            (callers_dir / f"noise_{index}.py").write_text(
                f"def noise_{index}():\n    return {index}\n",
                encoding="utf-8",
            )

        ctx = _FakeCtx()
        ProjectManagementService(ctx).initialize_project(str(tmp_root))
        IndexManagementService(ctx).rebuild_deep_index()

        started = time.perf_counter()
        result = ContextEngineService(ctx).get_context(
            target="app/core.py",
            intent="before_edit",
            symbol="target_function",
            include_source=False,
            change_type="signature_change",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        direct_files = {item.get("file") for item in result.get("direct_callers", [])}
        checks = {
            "all_5_callers_found": set(caller_files).issubset(direct_files),
            "no_noise_false_positives": not any(
                (item or "").startswith("callers/noise_") for item in direct_files
            ),
            "latency_within_budget": elapsed_ms <= latency_ms_budget,
            "confidence_high_enough": result.get("confidence") in {"high", "medium"},
        }
        return {
            "fixture": f"synthetic_large_repo_{file_count}_files",
            "passed": all(checks.values()),
            "checks": checks,
            "elapsed_ms": elapsed_ms,
            "latency_ms_budget": latency_ms_budget,
            "summary": {
                "file_count": file_count,
                "direct_caller_count": len(direct_files),
            },
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _evaluate_benchmark_large(
    file_count: int = 10000,
    true_caller_count: int = 20,
    bm25_seed_symbols: int = 20,
) -> dict[str, Any]:
    """Formal precision/recall benchmark on a synthetic ``file_count`` repo.

    Produces three metrics suitable for cross-run comparison:

    * ``references_recall``: fraction of true callers returned by
      ``find_references`` (surfaced via ``direct_callers``).
    * ``references_precision``: fraction of reported callers that are
      real callers (i.e. not the noise-named files).
    * ``bm25_recall_at_5``: for a synthetic "needle" symbol planted in
      one file, measures whether the top-5 BM25 hits include its file.

    Latency percentiles are measured across a handful of repeated
    queries so the report surfaces both p50 and p95.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="blindspot_bench_"))
    try:
        app_dir = tmp_root / "app"
        callers_dir = tmp_root / "callers"
        noise_dir = tmp_root / "noise"
        app_dir.mkdir()
        callers_dir.mkdir()
        noise_dir.mkdir()

        (app_dir / "core.py").write_text(
            "def target_function(x):\n    return x * 2\n",
            encoding="utf-8",
        )
        # Plant a unique "needle" symbol for BM25 recall check
        needle_dir = tmp_root / "app" / "needle"
        needle_dir.mkdir()
        (needle_dir / "handler.py").write_text(
            "def process_payment_refund(invoice_id):\n"
            "    '''Reverse a captured charge and notify the downstream ledger.'''\n"
            "    return invoice_id\n",
            encoding="utf-8",
        )

        true_callers = set()
        for index in range(true_caller_count):
            name = f"caller_{index}.py"
            true_callers.add(f"callers/{name}")
            (callers_dir / name).write_text(
                "from app.core import target_function\n\n"
                f"def do_{index}():\n"
                f"    return target_function({index})\n",
                encoding="utf-8",
            )
        # Fill remainder with noise files.
        remaining = file_count - (2 + true_caller_count)
        for index in range(remaining):
            (noise_dir / f"noise_{index}.py").write_text(
                f"def noise_{index}():\n    return {index}\n",
                encoding="utf-8",
            )

        ctx = _FakeCtx()
        ProjectManagementService(ctx).initialize_project(str(tmp_root))
        idx_start = time.perf_counter()
        IndexManagementService(ctx).rebuild_deep_index()
        index_ms = int((time.perf_counter() - idx_start) * 1000)

        # Reference recall/precision
        latencies: list[int] = []
        for _ in range(5):
            t0 = time.perf_counter()
            result = ContextEngineService(ctx).get_context(
                target="app/core.py",
                intent="before_edit",
                symbol="target_function",
                include_source=False,
                change_type="signature_change",
                max_related=max(50, true_caller_count * 2),
            )
            latencies.append(int((time.perf_counter() - t0) * 1000))

        reported = {item.get("file") for item in result.get("direct_callers", []) if item.get("file")}
        true_hits = reported & true_callers
        false_positives = {
            f for f in reported
            if f not in true_callers and not f.endswith("core.py")
        }
        recall = len(true_hits) / max(1, len(true_callers))
        precision = len(true_hits) / max(1, len(reported))

        # BM25 recall@5 on the planted needle
        from blindspot.indexing.sqlite_index_manager import SQLiteIndexManager
        mgr = SQLiteIndexManager()
        mgr.set_project_path(str(tmp_root))
        bm25_latencies: list[int] = []
        bm25_hit = False
        for _ in range(3):
            t0 = time.perf_counter()
            ranked = mgr.search_symbols(
                "payment refund ledger invoice reverse charge", limit=5
            )
            bm25_latencies.append(int((time.perf_counter() - t0) * 1000))
            if any(r.get("file") == "app/needle/handler.py" for r in (ranked or [])):
                bm25_hit = True

        return {
            "fixture": f"benchmark_{file_count}_files",
            "passed": recall == 1.0 and precision == 1.0 and bm25_hit,
            "metrics": {
                "references_recall": round(recall, 4),
                "references_precision": round(precision, 4),
                "bm25_recall_at_5": 1.0 if bm25_hit else 0.0,
                "false_positives": sorted(false_positives)[:5],
                "false_negatives": sorted(true_callers - reported)[:5],
            },
            "latency_ms": {
                "index_build": index_ms,
                "get_context": _latency_percentiles(latencies),
                "bm25_query": _latency_percentiles(bm25_latencies),
            },
            "scale": {
                "file_count": file_count,
                "true_callers": true_caller_count,
                "reported_callers": len(reported),
            },
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _latency_percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p95": 0, "p99": 0, "max": 0}
    ordered = sorted(values)

    def pct(p: float) -> int:
        if not ordered:
            return 0
        idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
        return ordered[idx]

    return {"p50": pct(50), "p95": pct(95), "p99": pct(99), "max": ordered[-1]}


def _probe_real_project(project_path: str, queries: int = 10) -> dict[str, Any]:
    """Run a bounded set of BM25 probes against a user-supplied repo.

    Useful for sanity-checking latency and retrieval quality on a real
    polyglot codebase. Does not assert correctness: returns timing and
    the top few retrieval hits so a human can audit.
    """
    from blindspot.indexing.sqlite_index_manager import SQLiteIndexManager
    mgr = SQLiteIndexManager()
    mgr.set_project_path(os.path.abspath(project_path))
    idx_started = time.perf_counter()
    mgr.build_index(force_rebuild=True)
    mgr.load_index()
    index_ms = int((time.perf_counter() - idx_started) * 1000)

    stats = mgr.get_index_stats()

    probe_terms = [
        "save user", "find by id", "render view", "parse config",
        "validate input", "http response", "test coverage", "load cache",
        "authenticate user", "database connection",
    ][:queries]

    probe_latencies: list[int] = []
    probe_samples: list[dict[str, Any]] = []
    for term in probe_terms:
        t0 = time.perf_counter()
        ranked = mgr.search_symbols(term, limit=5)
        probe_latencies.append(int((time.perf_counter() - t0) * 1000))
        probe_samples.append({
            "query": term,
            "top": [
                {"file": r.get("file"), "symbol": r.get("short_name"), "score": r.get("score")}
                for r in (ranked or [])[:3]
            ],
        })
    return {
        "project_path": project_path,
        "index_build_ms": index_ms,
        "index_stats": {
            "files": stats.get("indexed_files"),
            "symbols": stats.get("total_symbols"),
            "languages": stats.get("languages"),
        },
        "probe_latency_ms": _latency_percentiles(probe_latencies),
        "probe_samples": probe_samples,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Blindspot context-quality eval")
    parser.add_argument("--large", action="store_true",
                        help="Run the 5k-file synthetic scale test in addition to the 500-file one")
    parser.add_argument("--benchmark", action="store_true",
                        help="Run the 10k-file formal recall/precision benchmark (opt-in; slower)")
    parser.add_argument("--benchmark-files", type=int, default=10000,
                        help="File count for the formal benchmark (default 10000)")
    parser.add_argument("--project", type=str, default=None,
                        help="Optional path to a real project; runs BM25 probes and latency stats")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    results = [_evaluate_fixture(root, fixture) for fixture in FIXTURES]
    results.append(_evaluate_synthetic_large_repo())
    if args.large:
        results.append(_evaluate_synthetic_large_repo(file_count=5000, latency_ms_budget=15000))
    if args.benchmark:
        results.append(_evaluate_benchmark_large(file_count=args.benchmark_files))

    passed = sum(1 for item in results if item["passed"])
    payload: dict[str, Any] = {
        "fixtures": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }

    if args.project:
        payload["real_project_probe"] = _probe_real_project(args.project)

    print(json.dumps(payload, indent=2))
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
