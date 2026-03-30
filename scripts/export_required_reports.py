#!/usr/bin/env python3
"""Export the 4 mandatory release reports as individual JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from blindspot.services.safety_orchestration_service import SafetyOrchestrationService


class _Lifespan:
    def __init__(self, base_path: str):
        self.base_path = base_path
        self.settings = None
        self.file_count = 0


class _RequestContext:
    def __init__(self, base_path: str):
        self.lifespan_context = _Lifespan(base_path)


class _Context:
    def __init__(self, base_path: str):
        self.request_context = _RequestContext(base_path)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export required release reports")
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--out-dir", default=".blindspot/output/reports")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--closure-days", type=int, default=14)
    args = parser.parse_args()

    base = str(Path(args.project_path).resolve())
    out = Path(base) / args.out_dir
    svc = SafetyOrchestrationService(_Context(base))

    conformance = svc.conformance_matrix()
    gate = svc.gate_evidence_pack(limit=500)
    kpi = svc.kpi_report(window_days=max(1, args.window_days))
    risk = svc.open_risk_register(closure_days=max(1, args.closure_days), limit=500)

    _write(out / "1_conformance_matrix.json", conformance)
    _write(out / "2_gate_evidence_pack.json", gate)
    _write(out / "3_kpi_report.json", kpi)
    _write(out / "4_open_risk_register.json", risk)

    print(f"written={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
