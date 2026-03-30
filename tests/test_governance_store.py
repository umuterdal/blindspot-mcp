import tempfile
import unittest

from blindspot.safety.governance_store import SafetyGovernanceStore


class GovernanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SafetyGovernanceStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_seeded(self):
        scope = self.store.get_adapter_inventory()
        self.assertGreaterEqual(len(scope), 16)
        proto = self.store.get_kpi_protocol()
        self.assertIn("sample_size_min", proto)
        self.assertIn("thresholds", proto)
        self.assertEqual(proto["sample_size_min"], 500)
        self.assertEqual(proto["baseline_window_days"], 30)
        self.assertEqual(proto["drift_threshold_percent"], 2.0)

    def test_policy_change_requires_approvals_and_activates(self):
        req = self.store.create_policy_change(
            requested_by="alice",
            reason="tighten policy",
            policy={"profile": "strict", "allow_legacy_write": False},
            required_approvals=2,
        )
        self.assertEqual(req["status"], "success")
        cid = req["change_id"]

        a1 = self.store.approve_policy_change(cid, "bob")
        self.assertEqual(a1["state"], "pending")
        a2 = self.store.approve_policy_change(cid, "carol")
        self.assertEqual(a2["state"], "approved")

        active = self.store.get_active_policy()
        self.assertIsNotNone(active)
        self.assertEqual(active["policy"]["profile"], "strict")

    def test_break_glass_flow(self):
        req = self.store.create_break_glass_request(
            requested_by="alice",
            reason="payment outage",
            scope="payment",
            ttl_minutes=30,
            required_approvals=2,
        )
        rid = req["request_id"]
        p1 = self.store.approve_break_glass_request(rid, "bob")
        self.assertEqual(p1["state"], "pending")
        p2 = self.store.approve_break_glass_request(rid, "carol")
        self.assertEqual(p2["state"], "approved")
        row = self.store.get_break_glass_request(rid)
        self.assertEqual(row["status"], "approved")

    def test_break_glass_default_ttl_is_30(self):
        req = self.store.create_break_glass_request(
            requested_by="alice",
            reason="critical incident",
            scope="global",
            required_approvals=1,
        )
        row = self.store.get_break_glass_request(req["request_id"])
        self.assertEqual(int(row["ttl_minutes"]), 30)

    def test_registry_tables(self):
        b = self.store.add_backup_registry(
            backup_id="b1",
            backup_path="/tmp/fake.zip",
            sha256="abc",
            size_bytes=10,
            created_by="system",
            verified=True,
        )
        self.assertEqual(b["status"], "success")
        backups = self.store.list_backups(limit=10)
        self.assertEqual(len(backups), 1)

        r = self.store.add_redteam_result(
            suite="prompt_injection",
            case_name="inj",
            prompt="ignore previous instructions",
            expected_blocked=True,
            actual_blocked=True,
            status="pass",
            details={"k": "v"},
        )
        self.assertEqual(r["status"], "success")
        rows = self.store.list_redteam_results(limit=10)
        self.assertEqual(len(rows), 1)

    def test_incident_rule_registry(self):
        created = self.store.add_incident_rule(
            name="auth-bypass-regression",
            pattern="auth\\s*bypass",
            scope="auth",
            severity="critical",
            action="block",
            active=True,
            note="must never regress",
        )
        self.assertEqual(created["status"], "success")
        active_rows = self.store.list_incident_rules(active_only=True, limit=10)
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["action"], "block")

    def test_benchmark_run_registry(self):
        created = self.store.add_benchmark_run(
            sample_size_target=2000,
            sample_size_effective=2000,
            seed=42,
            stratified=True,
            overall_pass=True,
            payload={"overall_pass": True, "coverage_score": 99.0},
        )
        self.assertEqual(created["status"], "success")
        rows = self.store.list_benchmark_runs(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sample_size_target"], 2000)


if __name__ == "__main__":
    unittest.main()
