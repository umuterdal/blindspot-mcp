import os
import tempfile
import unittest
import warnings

from blindspot.services.safety_orchestration_service import SafetyOrchestrationService


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


class SafetyOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = _FakeCtx(self.tmp.name)
        self.svc = SafetyOrchestrationService(self.ctx)
        os.makedirs(os.path.join(self.tmp.name, ".blindspot"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self, content: str) -> None:
        with open(os.path.join(self.tmp.name, ".blindspot.yaml"), "w", encoding="utf-8") as f:
            f.write(content)

    def test_policy_status(self):
        status = self.svc.get_policy_status()
        self.assertEqual(status["status"], "success")
        self.assertIn("policy_hash", status)

    def test_security_suite(self):
        suite = self.svc.run_security_quality_suite(include_redteam=True)
        self.assertEqual(suite["status"], "success")
        self.assertIn("checks", suite)

    def test_break_glass_token_allows_critical_override(self):
        blocked = self.svc.run_policy_evaluation(
            feature_spec="Fix auth token refresh path",
            stage="write",
            target_file="src/auth/service.py",
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("critical_path_approval", blocked.get("checks", []))

        req = self.svc.request_break_glass(
            requested_by="alice",
            reason="prod outage",
            scope="auth,payment,webhook",
            ttl_minutes=30,
            required_approvals=1,
        )
        self.assertEqual(req["status"], "success")
        approve = self.svc.approve_break_glass(req["request_id"], "bob")
        self.assertEqual(approve["status"], "success")
        token = approve.get("break_glass_token")
        self.assertTrue(token)

        result = self.svc.run_policy_evaluation(
            feature_spec="Fix auth token refresh path",
            stage="write",
            target_file="src/auth/service.py",
            override_token="{}",
            break_glass_token=token,
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("break_glass_verified", result.get("checks", []))

    def test_rollout_stage_failure_triggers_rollback(self):
        rid = "release-test"
        self.svc.create_rollout_plan(rid)
        result = self.svc.execute_rollout_stage(
            release_id=rid,
            stage="canary",
            traffic_percent=1,
            smoke_commands=["false"],
            auto_rollback=True,
        )
        self.assertEqual(result["status"], "blocked")
        status = self.svc.get_rollout_status(rid)
        self.assertEqual(status["status"], "success")
        self.assertGreater(status["total_events"], 0)

    def test_rollout_plan_defaults_are_5_25_100(self):
        rid = "release-defaults"
        plan = self.svc.create_rollout_plan(rid)
        self.assertEqual(plan["status"], "success")
        percents = [int(s["traffic_percent"]) for s in plan["stages"]]
        self.assertEqual(percents, [5, 25, 100])

    def test_release_readiness_includes_policy_hash_consistency(self):
        report = self.svc.release_readiness_report(window_days=14, closure_days=14, include_security_suite=True)
        self.assertEqual(report["status"], "success")
        self.assertIn("policy_hash_consistency", report["flags"])
        self.assertTrue(report["flags"]["policy_hash_consistency"])
        self.assertIn("mutation_property_fuzz", report["flags"])
        self.assertIn("benchmark_harness", report["flags"])
        details = report["reports"]["policy_hash_consistency"]
        self.assertEqual(details["status"], "success")
        self.assertEqual(details["stage_statuses"]["write"], "success")
        self.assertEqual(details["stage_statuses"]["merge"], "success")
        self.assertEqual(details["stage_statuses"]["deploy"], "success")

    def test_conformance_matrix_uses_extended_10_method_contract(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            matrix = self.svc.conformance_matrix()
        self.assertEqual(matrix["status"], "success")
        required = matrix["required_methods"]
        self.assertEqual(len(required), 10)
        self.assertIn("contract_replay", required)
        self.assertIn("ui_regression_smoke", required)

    def test_backup_drill(self):
        drill = self.svc.run_dr_drill(created_by="test")
        self.assertEqual(drill["status"], "success")
        backup_id = drill["backup"]["backup_id"]
        dry = self.svc.restore_audit_backup(backup_id=backup_id, dry_run=True)
        self.assertEqual(dry["status"], "success")
        self.assertTrue(dry["dry_run"])

    def test_uncertainty_fail_closed_blocks_low_confidence(self):
        self._write_config(
            "policy:\n"
            "  profile: strict\n"
            "  allow_legacy_write: false\n"
            "  min_confidence_write: 0.95\n"
        )
        blocked = self.svc.run_policy_evaluation(
            feature_spec="Update docs text",
            stage="write",
            target_file="docs/readme.md",
            confidence_score=0.2,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("uncertainty_fail_closed", blocked.get("checks", []))

    def test_incident_memory_rule_blocks_matching_change(self):
        created = self.svc.record_incident_rule(
            name="prevent-legacy-delete",
            pattern="legacy-delete",
            scope="global",
            action="block",
        )
        self.assertEqual(created["status"], "success")
        blocked = self.svc.run_policy_evaluation(
            feature_spec="Re-introduce legacy-delete flow",
            stage="write",
            target_file="src/service.py",
            confidence_score=0.99,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("incident_memory", blocked.get("checks", []))

    def test_patch_primitive_mismatch_blocks_safe_implement(self):
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        target = os.path.join(self.tmp.name, "src", "main.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("x = 1\n")

        result = self.svc.safe_implement(
            feature_spec="replace x assignment",
            target_file="src/main.py",
            search="x = 1",
            replace="x = 2",
            patch_primitive="symbol_replace",
            confidence_score=0.99,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("patch_primitive", result)

    def test_mutation_property_fuzz_gate_blocks_on_failure(self):
        self._write_config(
            "quality_gates:\n"
            "  enabled: true\n"
            "  enforce_for_write: true\n"
            "  timeout_seconds: 30\n"
            "  mutation_command: \"false\"\n"
            "  property_command: \"false\"\n"
            "  fuzz_command: \"false\"\n"
        )
        result = self.svc.run_mutation_property_fuzz_suite(enforce=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["suite_status"], "fail")
        self.assertGreaterEqual(len(result.get("blocking_checks", [])), 1)

    def test_runtime_manifest_patch_primitives_and_benchmark(self):
        manifest = self.svc.get_runtime_manifest()
        self.assertEqual(manifest["status"], "success")
        self.assertIn("runtime_fingerprint", manifest)
        primitives = self.svc.list_patch_primitives()
        self.assertEqual(primitives["status"], "success")
        self.assertIn("search_replace", primitives["primitives"])
        benchmark = self.svc.run_benchmark_harness(sample_size=2000, seed=42, stratified=True)
        self.assertEqual(benchmark["status"], "success")
        self.assertEqual(benchmark["benchmark"]["sample_size_target"], 2000)
        runs = self.svc.list_benchmark_runs(limit=5)
        self.assertEqual(runs["status"], "success")
        self.assertGreaterEqual(runs["total"], 1)

    def test_safe_implement_runtime_budget_blocks(self):
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        target = os.path.join(self.tmp.name, "src", "main.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("x = 1\n")

        result = self.svc.safe_implement(
            feature_spec="tiny edit",
            target_file="src/main.py",
            search="x = 1",
            replace="x = 2",
            confidence_score=0.99,
            runtime_budget_seconds=0,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Runtime budget exceeded", result["message"])

    def test_fast_path_targeted_tests_can_block(self):
        self._write_config(
            "execution:\n"
            "  profile: fast_path\n"
            "  write_quality_mode: targeted\n"
            "  run_full_quality_after_write: false\n"
            "quality_gates:\n"
            "  targeted_tests_enabled: true\n"
            "  targeted_test_command: \"false\"\n"
            "  mutation_command: \"true\"\n"
            "  property_command: \"true\"\n"
            "  fuzz_command: \"true\"\n"
        )

        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "tests"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "src", "main.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        with open(os.path.join(self.tmp.name, "tests", "test_main.py"), "w", encoding="utf-8") as f:
            f.write("import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")

        result = self.svc.safe_implement(
            feature_spec="tiny edit",
            target_file="src/main.py",
            search="x = 1",
            replace="x = 2",
            confidence_score=0.99,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("targeted_tests", result)

    def test_warm_cache_hits_on_repeated_precheck(self):
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "src", "main.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")

        first = self.svc._run_prechecks_parallel(
            targets=["src/main.py"],
            symbol=None,
            parallelism=2,
            cache_ttl_seconds=120,
        )
        second = self.svc._run_prechecks_parallel(
            targets=["src/main.py"],
            symbol=None,
            parallelism=2,
            cache_ttl_seconds=120,
        )
        self.assertGreaterEqual(first["cache"]["misses"], 1)
        self.assertGreaterEqual(second["cache"]["hits"], 1)

    def test_safe_implement_success_includes_speed_fields(self):
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        target = os.path.join(self.tmp.name, "src", "main.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("x = 1\n")

        result = self.svc.safe_implement(
            feature_spec="replace x assignment",
            target_file="src/main.py",
            search="x = 1",
            replace="x = 2",
            confidence_score=0.99,
            execution_profile="fast_path",
            runtime_budget_seconds=120,
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("execution_profile", result)
        self.assertIn("targeted_tests", result)


if __name__ == "__main__":
    unittest.main()
