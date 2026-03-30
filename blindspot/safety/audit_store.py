"""SQLite-backed audit + replay store for safety orchestration."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s()]{7,}\d)(?!\d)")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SafetyAuditStore:
    """Persistent audit and replay store with hash-chained log records."""

    def __init__(self, project_path: str):
        if not project_path:
            raise ValueError("project_path is required for SafetyAuditStore")
        self.project_path = project_path
        self.audit_dir = Path(project_path) / ".blindspot" / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.audit_dir / "audit.db"
        self.chain_path = self.audit_dir / "audit_chain.jsonl"
        self._secret = os.getenv("BLINDSPOT_AUDIT_KEY", "blindspot-default-audit-key")
        self._init_schema()

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
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    feature_spec TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assumptions (
                    assumption_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nonces (
                    nonce TEXT PRIMARY KEY,
                    used_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_cost (
                    day TEXT PRIMARY KEY,
                    amount REAL NOT NULL
                )
                """
            )

    @staticmethod
    def _redact_text(text: str) -> str:
        # Order matters: redact card first so phone regex doesn't partially mask it.
        text = _CARD_RE.sub("[REDACTED_CARD]", text)
        text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
        return text

    def redact_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {k: self.redact_payload(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return [self.redact_payload(v) for v in payload]
        if isinstance(payload, str):
            return self._redact_text(payload)
        return payload

    def _get_last_hash(self) -> str:
        if not self.chain_path.exists():
            return ""
        try:
            with self.chain_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return ""
            last = json.loads(lines[-1])
            return str(last.get("hash", ""))
        except Exception:
            return ""

    def _sign_record(self, record: Dict[str, Any], prev_hash: str) -> str:
        data = json.dumps(record, sort_keys=True, ensure_ascii=False)
        msg = f"{prev_hash}|{data}".encode("utf-8")
        return hmac.new(self._secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def start_run(self, action: str, feature_spec: str, spec_hash: str, policy_hash: str) -> str:
        run_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(run_id, action, feature_spec, spec_hash, policy_hash, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, action, feature_spec, spec_hash, policy_hash, "running", now, now),
            )
        return run_id

    def set_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                (status, _utc_now(), run_id),
            )

    def add_event(self, run_id: str, stage: str, status: str, payload: Dict[str, Any]) -> None:
        redacted = self.redact_payload(payload)
        payload_json = json.dumps(redacted, ensure_ascii=False, default=str)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events(run_id, stage, status, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (run_id, stage, status, payload_json, now),
            )

        chain_record = {
            "ts": now,
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "payload": redacted,
        }
        prev_hash = self._get_last_hash()
        chain_hash = self._sign_record(chain_record, prev_hash)
        envelope = {
            "prev_hash": prev_hash,
            "hash": chain_hash,
            "record": chain_record,
        }
        with self.chain_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")

    def add_assumptions(self, run_id: str, assumptions: List[str]) -> List[str]:
        ids: List[str] = []
        now = _utc_now()
        with self._connect() as conn:
            for item in assumptions:
                aid = str(uuid.uuid4())
                ids.append(aid)
                conn.execute(
                    """
                    INSERT INTO assumptions(assumption_id, run_id, text, status, evidence, note, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (aid, run_id, item, "open", None, None, now, now),
                )
        return ids

    def list_assumptions(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM assumptions WHERE run_id=? ORDER BY created_at ASC", (run_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM assumptions ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def resolve_assumption(
        self,
        assumption_id: str,
        status: str,
        evidence: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in {"verified", "rejected", "resolved", "open"}:
            return {"status": "error", "message": f"Invalid assumption status: {status}"}

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM assumptions WHERE assumption_id=?", (assumption_id,)
            ).fetchone()
            if not row:
                return {"status": "error", "message": f"Assumption not found: {assumption_id}"}

            conn.execute(
                """
                UPDATE assumptions
                SET status=?, evidence=?, note=?, updated_at=?
                WHERE assumption_id=?
                """,
                (status, evidence, note, _utc_now(), assumption_id),
            )
            updated = conn.execute(
                "SELECT * FROM assumptions WHERE assumption_id=?", (assumption_id,)
            ).fetchone()

        return {"status": "success", "assumption": dict(updated)}

    def has_open_assumptions(self, run_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS c FROM assumptions WHERE run_id=? AND status NOT IN ('verified','resolved')",
                (run_id,),
            ).fetchone()
        return bool(row and row["c"] > 0)

    def use_nonce(self, nonce: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT nonce FROM nonces WHERE nonce=?", (nonce,)).fetchone()
            if row:
                return False
            conn.execute("INSERT INTO nonces(nonce, used_at) VALUES(?, ?)", (nonce, _utc_now()))
        return True

    def add_cost(self, amount: float) -> Dict[str, Any]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute("SELECT amount FROM daily_cost WHERE day=?", (day,)).fetchone()
            current = float(row["amount"]) if row else 0.0
            updated = current + max(0.0, amount)
            if row:
                conn.execute("UPDATE daily_cost SET amount=? WHERE day=?", (updated, day))
            else:
                conn.execute("INSERT INTO daily_cost(day, amount) VALUES(?, ?)", (day, updated))
        return {"day": day, "amount": round(updated, 4)}

    def get_daily_cost(self) -> Dict[str, Any]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute("SELECT amount FROM daily_cost WHERE day=?", (day,)).fetchone()
        return {"day": day, "amount": round(float(row["amount"]) if row else 0.0, 4)}

    def list_runs(self, statuses: Optional[List[str]] = None, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 200), 5000))
        with self._connect() as conn:
            if statuses:
                placeholders = ",".join(["?"] * len(statuses))
                rows = conn.execute(
                    f"""
                    SELECT * FROM runs
                    WHERE status IN ({placeholders})
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (*statuses, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def list_events(
        self,
        run_id: Optional[str] = None,
        stages: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 10000))
        with self._connect() as conn:
            if run_id and stages:
                placeholders = ",".join(["?"] * len(stages))
                rows = conn.execute(
                    f"""
                    SELECT * FROM events
                    WHERE run_id=? AND stage IN ({placeholders})
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (run_id, *stages, limit),
                ).fetchall()
            elif run_id:
                rows = conn.execute(
                    "SELECT * FROM events WHERE run_id=? ORDER BY id ASC LIMIT ?",
                    (run_id, limit),
                ).fetchall()
            elif stages:
                placeholders = ",".join(["?"] * len(stages))
                rows = conn.execute(
                    f"""
                    SELECT * FROM events
                    WHERE stage IN ({placeholders})
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (*stages, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            payload_raw = item.get("payload_json")
            if isinstance(payload_raw, str):
                try:
                    item["payload"] = json.loads(payload_raw)
                except Exception:
                    item["payload"] = payload_raw
            out.append(item)
        return out

    @staticmethod
    def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def kpi_report(self, window_days: int = 14) -> Dict[str, Any]:
        from datetime import timedelta

        window_days = max(1, min(int(window_days or 14), 365))
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        runs = self.list_runs(limit=5000)
        scoped_runs = []
        for run in runs:
            created = self._parse_iso_dt(run.get("created_at"))
            if created and created >= cutoff:
                scoped_runs.append(run)

        run_ids = {r.get("run_id") for r in scoped_runs if r.get("run_id")}
        events = self.list_events(limit=10000)
        scoped_events = [e for e in events if e.get("run_id") in run_ids]

        total_runs = len(scoped_runs)
        success_runs = sum(1 for r in scoped_runs if r.get("status") == "success")
        gate_events = [
            e for e in scoped_events
            if e.get("stage") in {"policy_write", "policy_merge", "policy_deploy"}
        ]
        gate_pass = sum(1 for e in gate_events if e.get("status") == "success")

        rollback_runs = {e.get("run_id") for e in scoped_events if e.get("stage") == "rollback"}
        critical_regressions = sum(
            1
            for e in scoped_events
            if e.get("stage") in {"critical_regression", "regression"} and e.get("status") != "success"
        )

        gate_pass_rate = (gate_pass / len(gate_events) * 100.0) if gate_events else 100.0
        first_pass_rate = (success_runs / total_runs * 100.0) if total_runs else 100.0
        rollback_rate = (len(rollback_runs) / total_runs * 100.0) if total_runs else 0.0

        thresholds = {
            "gate_pass_rate_min": 95.0,
            "first_pass_rate_min": 90.0,
            "rollback_rate_max": 2.0,
            "critical_regressions_max": 0,
        }
        checks = {
            "gate_pass_rate": gate_pass_rate >= thresholds["gate_pass_rate_min"],
            "first_pass_rate": first_pass_rate >= thresholds["first_pass_rate_min"],
            "rollback_rate": rollback_rate <= thresholds["rollback_rate_max"],
            "critical_regressions": critical_regressions <= thresholds["critical_regressions_max"],
        }

        return {
            "status": "success",
            "report": "KPI Report",
            "window_days": window_days,
            "totals": {
                "runs": total_runs,
                "success_runs": success_runs,
                "gate_events": len(gate_events),
                "rollback_runs": len(rollback_runs),
                "critical_regressions": critical_regressions,
            },
            "kpis": {
                "gate_pass_rate": round(gate_pass_rate, 2),
                "first_pass_rate": round(first_pass_rate, 2),
                "rollback_rate": round(rollback_rate, 2),
                "critical_regressions": critical_regressions,
            },
            "thresholds": thresholds,
            "checks": checks,
            "overall_pass": all(checks.values()),
        }

    def replay_run(self, run_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            events = conn.execute(
                "SELECT stage, status, payload_json, created_at FROM events WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()

        if not run:
            return {"status": "error", "message": f"Run not found: {run_id}"}

        chain_ok = True
        last_hash = ""
        if self.chain_path.exists():
            with self.chain_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        envelope = json.loads(line)
                    except Exception:
                        chain_ok = False
                        break
                    record = envelope.get("record", {})
                    if record.get("run_id") != run_id:
                        last_hash = envelope.get("hash", last_hash)
                        continue
                    prev_hash = envelope.get("prev_hash", "")
                    if prev_hash != last_hash:
                        chain_ok = False
                        break
                    expected = self._sign_record(record, prev_hash)
                    if expected != envelope.get("hash", ""):
                        chain_ok = False
                        break
                    last_hash = envelope.get("hash", "")

        return {
            "status": "success",
            "run": dict(run),
            "events": [
                {
                    "stage": r["stage"],
                    "status": r["status"],
                    "payload": json.loads(r["payload_json"]),
                    "created_at": r["created_at"],
                }
                for r in events
            ],
            "chain_verified": chain_ok,
        }
