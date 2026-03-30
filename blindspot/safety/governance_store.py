"""Governance and operations store for production safety controls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SafetyGovernanceStore:
    """Persistent store for governance, rollout, DR, and release controls."""

    DEFAULT_PROTOCOL = {
        "sample_size_min": 500,
        "baseline_window_days": 30,
        "measurement_method": "rolling_window",
        "error_budget_percent": 2.0,
        "drift_threshold_percent": 2.0,
        "allow_bootstrap_if_empty": True,
        "thresholds": {
            "gate_pass_rate_min": 95.0,
            "first_pass_rate_min": 90.0,
            "rollback_rate_max": 2.0,
            "critical_regressions_max": 0,
        },
    }

    DEFAULT_SCOPE = [
        {"framework": "laravel", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "nextjs", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "nuxt", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "sveltekit", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "django", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "spring", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "express", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "go", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "rails", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "fastapi", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "flutter", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "aspnet", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "reactnative", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "nestjs", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "rust", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
        {"framework": "phoenix", "owner": "unassigned", "due_date": "", "done_criteria": "all required adapter methods pass + smoke checks"},
    ]

    @classmethod
    def _normalize_protocol(cls, protocol: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(cls.DEFAULT_PROTOCOL)
        if isinstance(protocol, dict):
            merged.update(protocol)
            if isinstance(cls.DEFAULT_PROTOCOL.get("thresholds"), dict):
                t = dict(cls.DEFAULT_PROTOCOL["thresholds"])
                t.update(protocol.get("thresholds", {}) if isinstance(protocol.get("thresholds"), dict) else {})
                merged["thresholds"] = t

        # Strict floor/ceiling normalization for release-quality defaults.
        merged["sample_size_min"] = max(500, int(merged.get("sample_size_min", 500)))
        merged["baseline_window_days"] = max(30, int(merged.get("baseline_window_days", 30)))
        merged["drift_threshold_percent"] = min(2.0, float(merged.get("drift_threshold_percent", 2.0)))
        merged["error_budget_percent"] = min(2.0, float(merged.get("error_budget_percent", 2.0)))
        return merged

    def __init__(self, project_path: str):
        if not project_path:
            raise ValueError("project_path is required for SafetyGovernanceStore")
        self.project_path = project_path
        self.audit_dir = Path(project_path) / ".blindspot" / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.audit_dir / "governance.db"
        self._init_schema()
        self._seed_defaults()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS adapter_inventory (
                    framework TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    done_criteria TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kpi_protocol (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    protocol_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_changes (
                    change_id TEXT PRIMARY KEY,
                    requested_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    required_approvals INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_change_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    change_id TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    note TEXT,
                    approved_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_policy (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    policy_json TEXT NOT NULL,
                    source_change_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS key_rotations (
                    rotation_id TEXT PRIMARY KEY,
                    key_name TEXT NOT NULL,
                    old_fingerprint TEXT NOT NULL,
                    new_fingerprint TEXT NOT NULL,
                    rotated_by TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS break_glass_requests (
                    request_id TEXT PRIMARY KEY,
                    requested_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    ttl_minutes INTEGER NOT NULL,
                    required_approvals INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    used_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS break_glass_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    note TEXT,
                    approved_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rollout_events (
                    event_id TEXT PRIMARY KEY,
                    release_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    traffic_percent REAL NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT,
                    command TEXT,
                    command_exit_code INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backup_registry (
                    backup_id TEXT PRIMARY KEY,
                    backup_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    restored_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS redteam_results (
                    result_id TEXT PRIMARY KEY,
                    suite TEXT NOT NULL,
                    case_name TEXT NOT NULL,
                    expected_blocked INTEGER NOT NULL,
                    actual_blocked INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_rules (
                    rule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    action TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    benchmark_id TEXT PRIMARY KEY,
                    sample_size_target INTEGER NOT NULL,
                    sample_size_effective INTEGER NOT NULL,
                    seed INTEGER NOT NULL,
                    stratified INTEGER NOT NULL,
                    overall_pass INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _seed_defaults(self) -> None:
        now = _utc_now()
        with self._connect() as conn:
            for item in self.DEFAULT_SCOPE:
                exists = conn.execute(
                    "SELECT framework FROM adapter_inventory WHERE framework=?",
                    (item["framework"],),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO adapter_inventory(framework, owner, due_date, done_criteria, status, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["framework"],
                        item["owner"],
                        item["due_date"],
                        item["done_criteria"],
                        "planned",
                        now,
                    ),
                )

            protocol_row = conn.execute("SELECT id FROM kpi_protocol WHERE id=1").fetchone()
            if not protocol_row:
                conn.execute(
                    "INSERT INTO kpi_protocol(id, protocol_json, updated_at) VALUES(1, ?, ?)",
                    (json.dumps(self.DEFAULT_PROTOCOL, ensure_ascii=False), now),
                )

    def get_adapter_inventory(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM adapter_inventory ORDER BY framework ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_adapter_inventory(
        self,
        framework: str,
        owner: str,
        due_date: str,
        done_criteria: str,
        status: str = "planned",
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO adapter_inventory(framework, owner, due_date, done_criteria, status, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(framework) DO UPDATE SET
                    owner=excluded.owner,
                    due_date=excluded.due_date,
                    done_criteria=excluded.done_criteria,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (framework, owner, due_date, done_criteria, status, _utc_now()),
            )
            row = conn.execute(
                "SELECT * FROM adapter_inventory WHERE framework=?",
                (framework,),
            ).fetchone()
        return dict(row) if row else {}

    def get_kpi_protocol(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT protocol_json, updated_at FROM kpi_protocol WHERE id=1").fetchone()
        if not row:
            return dict(self._normalize_protocol(self.DEFAULT_PROTOCOL))
        data = self._normalize_protocol(json.loads(row["protocol_json"]))
        data["updated_at"] = row["updated_at"]
        return data

    def set_kpi_protocol(self, protocol: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_protocol(protocol)
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kpi_protocol(id, protocol_json, updated_at)
                VALUES(1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET protocol_json=excluded.protocol_json, updated_at=excluded.updated_at
                """,
                (payload, now),
            )
        return {"status": "success", "protocol": normalized, "updated_at": now}

    def create_policy_change(
        self,
        requested_by: str,
        reason: str,
        policy: Dict[str, Any],
        required_approvals: int = 2,
    ) -> Dict[str, Any]:
        change_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_changes(change_id, requested_by, reason, policy_json, required_approvals, status, created_at, approved_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    change_id,
                    requested_by,
                    reason,
                    json.dumps(policy, ensure_ascii=False),
                    max(1, int(required_approvals)),
                    "pending",
                    now,
                ),
            )
        return {"status": "success", "change_id": change_id, "created_at": now}

    def approve_policy_change(self, change_id: str, approver: str, note: Optional[str] = None) -> Dict[str, Any]:
        now = _utc_now()
        with self._connect() as conn:
            change = conn.execute(
                "SELECT * FROM policy_changes WHERE change_id=?",
                (change_id,),
            ).fetchone()
            if not change:
                return {"status": "error", "message": f"Policy change not found: {change_id}"}

            existing = conn.execute(
                "SELECT id FROM policy_change_approvals WHERE change_id=? AND approver=?",
                (change_id, approver),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO policy_change_approvals(change_id, approver, note, approved_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (change_id, approver, note, now),
                )

            count_row = conn.execute(
                "SELECT COUNT(1) AS c FROM policy_change_approvals WHERE change_id=?",
                (change_id,),
            ).fetchone()
            approvals = int(count_row["c"]) if count_row else 0
            required = int(change["required_approvals"])

            status = "pending"
            if approvals >= required:
                status = "approved"
                conn.execute(
                    "UPDATE policy_changes SET status='approved', approved_at=? WHERE change_id=?",
                    (now, change_id),
                )
                conn.execute(
                    """
                    INSERT INTO active_policy(id, policy_json, source_change_id, updated_at)
                    VALUES(1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        policy_json=excluded.policy_json,
                        source_change_id=excluded.source_change_id,
                        updated_at=excluded.updated_at
                    """,
                    (change["policy_json"], change_id, now),
                )
            else:
                conn.execute(
                    "UPDATE policy_changes SET status='pending' WHERE change_id=?",
                    (change_id,),
                )

        return {
            "status": "success",
            "change_id": change_id,
            "approval_count": approvals,
            "required_approvals": required,
            "state": status,
        }

    def get_active_policy(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT policy_json, source_change_id, updated_at FROM active_policy WHERE id=1"
            ).fetchone()
        if not row:
            return None
        return {
            "policy": json.loads(row["policy_json"]),
            "source_change_id": row["source_change_id"],
            "updated_at": row["updated_at"],
        }

    def list_policy_changes(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 1000))
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM policy_changes WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM policy_changes ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["policy"] = json.loads(item.get("policy_json", "{}"))
            except Exception:
                item["policy"] = {}
            out.append(item)
        return out

    def add_key_rotation(
        self,
        key_name: str,
        old_value: str,
        new_value: str,
        rotated_by: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        rotation_id = str(uuid.uuid4())
        old_fp = hashlib.sha256(old_value.encode("utf-8")).hexdigest()[:16]
        new_fp = hashlib.sha256(new_value.encode("utf-8")).hexdigest()[:16]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO key_rotations(rotation_id, key_name, old_fingerprint, new_fingerprint, rotated_by, note, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (rotation_id, key_name, old_fp, new_fp, rotated_by, note, _utc_now()),
            )
        return {
            "status": "success",
            "rotation_id": rotation_id,
            "key_name": key_name,
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
        }

    def list_key_rotations(self, key_name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 1000))
        with self._connect() as conn:
            if key_name:
                rows = conn.execute(
                    "SELECT * FROM key_rotations WHERE key_name=? ORDER BY created_at DESC LIMIT ?",
                    (key_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM key_rotations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def create_break_glass_request(
        self,
        requested_by: str,
        reason: str,
        scope: str,
        ttl_minutes: int = 30,
        required_approvals: int = 2,
    ) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO break_glass_requests(request_id, requested_by, reason, scope, ttl_minutes, required_approvals, status, created_at, approved_at, used_at)
                VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)
                """,
                (
                    request_id,
                    requested_by,
                    reason,
                    scope,
                    max(1, int(ttl_minutes)),
                    max(1, int(required_approvals)),
                    now,
                ),
            )
        return {"status": "success", "request_id": request_id, "created_at": now}

    def approve_break_glass_request(self, request_id: str, approver: str, note: Optional[str] = None) -> Dict[str, Any]:
        now = _utc_now()
        with self._connect() as conn:
            req = conn.execute(
                "SELECT * FROM break_glass_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not req:
                return {"status": "error", "message": f"Break-glass request not found: {request_id}"}

            existing = conn.execute(
                "SELECT id FROM break_glass_approvals WHERE request_id=? AND approver=?",
                (request_id, approver),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO break_glass_approvals(request_id, approver, note, approved_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (request_id, approver, note, now),
                )

            count_row = conn.execute(
                "SELECT COUNT(1) AS c FROM break_glass_approvals WHERE request_id=?",
                (request_id,),
            ).fetchone()
            approvals = int(count_row["c"]) if count_row else 0
            required = int(req["required_approvals"])
            if approvals >= required:
                conn.execute(
                    "UPDATE break_glass_requests SET status='approved', approved_at=? WHERE request_id=?",
                    (now, request_id),
                )
                state = "approved"
            else:
                state = "pending"

        return {
            "status": "success",
            "request_id": request_id,
            "approval_count": approvals,
            "required_approvals": required,
            "state": state,
        }

    def get_break_glass_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            req = conn.execute(
                "SELECT * FROM break_glass_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not req:
                return None
            approvals = conn.execute(
                "SELECT approver, note, approved_at FROM break_glass_approvals WHERE request_id=? ORDER BY id ASC",
                (request_id,),
            ).fetchall()
        data = dict(req)
        data["approvals"] = [dict(r) for r in approvals]
        return data

    def mark_break_glass_used(self, request_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE break_glass_requests SET status='used', used_at=? WHERE request_id=?",
                (_utc_now(), request_id),
            )

    def add_rollout_event(
        self,
        release_id: str,
        stage: str,
        traffic_percent: float,
        status: str,
        note: Optional[str] = None,
        command: Optional[str] = None,
        command_exit_code: Optional[int] = None,
    ) -> Dict[str, Any]:
        event_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rollout_events(event_id, release_id, stage, traffic_percent, status, note, command, command_exit_code, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    release_id,
                    stage,
                    float(traffic_percent),
                    status,
                    note,
                    command,
                    command_exit_code,
                    _utc_now(),
                ),
            )
        return {"status": "success", "event_id": event_id}

    def get_rollout_events(self, release_id: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 500), 5000))
        with self._connect() as conn:
            if release_id:
                rows = conn.execute(
                    "SELECT * FROM rollout_events WHERE release_id=? ORDER BY created_at ASC LIMIT ?",
                    (release_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rollout_events ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def add_backup_registry(
        self,
        backup_id: str,
        backup_path: str,
        sha256: str,
        size_bytes: int,
        created_by: str,
        verified: bool,
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backup_registry(backup_id, backup_path, sha256, size_bytes, created_by, verified, created_at, restored_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (backup_id, backup_path, sha256, int(size_bytes), created_by, 1 if verified else 0, _utc_now()),
            )
        return {"status": "success", "backup_id": backup_id}

    def mark_backup_restored(self, backup_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE backup_registry SET restored_at=? WHERE backup_id=?",
                (_utc_now(), backup_id),
            )

    def list_backups(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 2000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backup_registry ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_redteam_result(
        self,
        suite: str,
        case_name: str,
        prompt: str,
        expected_blocked: bool,
        actual_blocked: bool,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rid = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO redteam_results(result_id, suite, case_name, expected_blocked, actual_blocked, status, prompt, details_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    suite,
                    case_name,
                    1 if expected_blocked else 0,
                    1 if actual_blocked else 0,
                    status,
                    prompt,
                    json.dumps(details or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
        return {"status": "success", "result_id": rid}

    def list_redteam_results(self, suite: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 200), 3000))
        with self._connect() as conn:
            if suite:
                rows = conn.execute(
                    "SELECT * FROM redteam_results WHERE suite=? ORDER BY created_at DESC LIMIT ?",
                    (suite, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM redteam_results ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details_json", "{}"))
            except Exception:
                item["details"] = {}
            out.append(item)
        return out

    def add_incident_rule(
        self,
        name: str,
        pattern: str,
        scope: str = "global",
        severity: str = "high",
        action: str = "block",
        active: bool = True,
        note: str = "",
    ) -> Dict[str, Any]:
        rule_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incident_rules(rule_id, name, pattern, scope, severity, action, active, note, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    name.strip() or "incident-rule",
                    pattern.strip(),
                    scope.strip() or "global",
                    severity.strip() or "high",
                    action.strip() or "block",
                    1 if active else 0,
                    note,
                    now,
                    now,
                ),
            )
        return {"status": "success", "rule_id": rule_id, "created_at": now}

    def list_incident_rules(self, active_only: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 200), 5000))
        with self._connect() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM incident_rules WHERE active=1 ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM incident_rules ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def add_benchmark_run(
        self,
        sample_size_target: int,
        sample_size_effective: int,
        seed: int,
        stratified: bool,
        overall_pass: bool,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        benchmark_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO benchmark_runs(benchmark_id, sample_size_target, sample_size_effective, seed, stratified, overall_pass, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark_id,
                    int(sample_size_target),
                    int(sample_size_effective),
                    int(seed),
                    1 if stratified else 0,
                    1 if overall_pass else 0,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
        return {"status": "success", "benchmark_id": benchmark_id, "created_at": now}

    def list_benchmark_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 2000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload_json", "{}"))
            except Exception:
                item["payload"] = {}
            out.append(item)
        return out
