#!/usr/bin/env python3
"""Run full production readiness gate and print required report paths/results."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Blindspot safety release gate")
    parser.add_argument("--project-path", required=True, help="Project root path")
    parser.add_argument("--window-days", type=int, default=30, help="KPI window days")
    parser.add_argument("--closure-days", type=int, default=14, help="Risk closure target days")
    parser.add_argument("--output", default=".blindspot/output/release_readiness.json", help="Output report path")
    args = parser.parse_args()

    base = str(Path(args.project_path).resolve())
    ctx = _Context(base)
    svc = SafetyOrchestrationService(ctx)

    report = svc.release_readiness_report(
        window_days=max(1, args.window_days),
        closure_days=max(1, args.closure_days),
        include_security_suite=True,
    )

    out_path = Path(base) / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"ready_for_release={report.get('ready_for_release')}")
    print(f"flags={report.get('flags', {})}")
    print(f"report_file={out_path}")
    return 0 if report.get("ready_for_release") else 2


if __name__ == "__main__":
    raise SystemExit(main())
