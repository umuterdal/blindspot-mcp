#!/usr/bin/env python3
"""
Run an end-to-end MCP tool smoke matrix against a real project path.

This script connects through MCP stdio, discovers all tools, invokes each tool
with safe/sample arguments, and writes a JSON report with pass/fail details.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _build_key_defaults(project_path: str) -> Dict[str, Any]:
    return {
        "file_path": "app/Http/Controllers/Public/ListingController.php",
        "target_file": ".blindspot/tmp/mcp_smoke_target.py",
        "symbol": "index",
        "symbol_name": "index",
        "controller": "Public/ListingController",
        "method": "index",
        "entry_point": "Public/ListingController",
        "view_path": "resources/views/public/listings/index.blade.php",
        "route_name": "login",
        "table_name": "providers",
        "table_or_model": "providers",
        "columns": ["id"],
        "pattern": "Route::get",
        "description": "modal",
        "class_name": "ListingController",
        "model_name": "Provider",
        "screen_name": "OnboardingScreen",
        "component_name": "OnboardingScreen",
        "store_name": "auth",
        "cache_key": "listing.*",
        "feature_spec": "smoke test",
        "reason": "smoke test",
        "requested_by": "smoke-bot",
        "approver": "smoke-approver",
        "name": "smoke-rule",
        "path": project_path,
        "framework": "laravel",
        "owner": "FW-LARAVEL-OWNER",
        "due_date": "2026-06-30",
        "done_criteria": "smoke pass",
        "release_id": "smoke-release",
        "change_id": "",
        "request_id": "",
        "backup_id": "",
        "run_id": "",
        "assumption_id": "",
        "status": "resolved",
        "old_name": "legacy_helper",
        "new_name": "modern_helper",
        "search": "x = legacy_helper()",
        "replace": "x = modern_helper()",
        "schema_entity": "providers",
        "schema_fields": ["id"],
        "risk_domains": ["quality"],
        "traffic_percent": 5,
        "stage": "canary",
        "sample_size_min": 500,
        "baseline_window_days": 30,
        "measurement_method": "rolling_window",
        "error_budget_percent": 2.0,
        "drift_threshold_percent": 2.0,
        "thresholds": {
            "gate_pass_rate_min": 95.0,
            "first_pass_rate_min": 90.0,
            "rollback_rate_max": 2.0,
            "critical_regressions_max": 0,
        },
        "policy": {
            "profile": "strict",
            "allow_legacy_write": False,
        },
    }


def _prepare_smoke_files(project_path: str) -> None:
    tmp_dir = Path(project_path) / ".blindspot" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    smoke_file = tmp_dir / "mcp_smoke_target.py"
    smoke_file.write_text(
        "def legacy_helper():\n"
        "    return 1\n\n"
        "x = legacy_helper()\n",
        encoding="utf-8",
    )


def _reset_smoke_file(project_path: str) -> None:
    _prepare_smoke_files(project_path)


async def _call_tool(
    session: ClientSession,
    name: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        result = await session.call_tool(name, args)
        text_items = [getattr(c, "text", "") for c in (result.content or []) if hasattr(c, "text")]
        text = text_items[0] if text_items else ""
        payload = _safe_json_loads(text)
        status = payload.get("status") if payload else None
        return {
            "ok": True,
            "status": status or "unknown",
            "head": text[:240].replace("\n", " "),
            "payload": payload,
            "has_payload": payload is not None,
        }
    except Exception as e:
        return {"ok": False, "status": "exception", "error": str(e)}


def _tool_overrides(name: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    smoke_file = defaults["target_file"]
    return {
        "analyze_queries": {"controller": defaults["controller"], "method": defaults["method"]},
        "apply_edit": {
            "file_path": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
        },
        "apply_edit_multi": {
            "file_edits": [
                {
                    "file_path": smoke_file,
                    "edits": [{"search": "x = modern_helper()", "replace": "x = legacy_helper()"}],
                }
            ]
        },
        "diff_preview": {
            "edits": [
                {
                    "file_path": smoke_file,
                    "search": "x = legacy_helper()",
                    "replace": "x = modern_helper()",
                }
            ]
        },
        "full_audit": {"focus": "performance"},
        "get_edit_region": {"file_path": defaults["file_path"], "symbol": defaults["symbol"]},
        "get_flow_map": {"entry_point": defaults["controller"], "method": defaults["method"]},
        "get_middleware_chain": {"route_name": defaults["route_name"]},
        "get_project_conventions": {"pattern_type": "validation"},
        "get_rn_flow_map": {"entry_point": defaults["screen_name"]},
        "get_rn_platform_specific": {"file_path": "mobile/app/screens/OnboardingScreen.tsx"},
        "get_rn_project_conventions": {"pattern_type": "navigation"},
        "get_rn_similar_patterns": {"description": "bottom sheet"},
        "get_route_map": {"filter_prefix": "api"},
        "get_similar_patterns": {"description": "form validation"},
        "get_symbol_body": {"file_path": defaults["file_path"], "symbol_name": defaults["symbol_name"]},
        "get_validation_chain": {"controller": defaults["controller"], "method": defaults["method"]},
        "goal_to_patch": {"feature_spec": "smoke spec"},
        "match_view_guards": {"file_path": defaults["file_path"], "symbol": defaults["symbol"]},
        "post_edit_checklist": {"file_path": defaults["file_path"]},
        "pre_edit_check": {"file_path": defaults["file_path"], "symbol_name": defaults["symbol_name"]},
        "pre_rn_edit_check": {
            "file_path": "mobile/app/screens/OnboardingScreen.tsx",
            "symbol_name": defaults["screen_name"],
        },
        "record_incident_rule": {
            "name": "smoke-no-legacy-delete",
            "pattern": "legacy-delete",
            "action": "block",
            "scope": "global",
        },
        "rename_symbol": {
            "file_path": smoke_file,
            "old_name": defaults["old_name"],
            "new_name": defaults["new_name"],
            "dry_run": True,
        },
        "request_break_glass": {
            "requested_by": defaults["requested_by"],
            "reason": "smoke verification",
            "scope": "quality",
            "ttl_minutes": 30,
            "required_approvals": 1,
        },
        "request_policy_change": {
            "requested_by": defaults["requested_by"],
            "reason": "smoke verification",
            "policy": defaults["policy"],
            "required_approvals": 1,
        },
        "restore_audit_backup": {"backup_id": defaults["backup_id"], "dry_run": True},
        "rotate_signing_key": {
            "key_name": "smoke-key",
            "old_value": "old-smoke-key",
            "new_value": "new-smoke-key",
            "rotated_by": defaults["requested_by"],
            "note": "smoke",
        },
        "run_policy_evaluation": {
            "feature_spec": "smoke-policy",
            "stage": "write",
            "confidence_score": 0.95,
            "estimated_escalation_cost": 1.0,
            "risk_domains": ["quality"],
            "target_file": smoke_file,
        },
        "safe_fix": {
            "feature_spec": "smoke safe_fix",
            "target_file": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
            "patch_primitive": "search_replace",
            "confidence_score": 0.95,
        },
        "safe_implement": {
            "feature_spec": "smoke safe_implement",
            "target_file": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
            "patch_primitive": "search_replace",
            "confidence_score": 0.95,
            "execution_profile": "fast_path",
            "runtime_budget_seconds": 60,
        },
        "safe_migrate": {
            "feature_spec": "smoke safe_migrate",
            "target_file": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
            "patch_primitive": "search_replace",
            "confidence_score": 0.95,
        },
        "safe_optimize": {
            "feature_spec": "smoke safe_optimize",
            "target_file": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
            "patch_primitive": "search_replace",
            "confidence_score": 0.95,
        },
        "safe_refactor": {
            "feature_spec": "smoke safe_refactor",
            "target_file": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
            "patch_primitive": "search_replace",
            "confidence_score": 0.95,
        },
        "search_code_advanced": {"pattern": "ListingController", "max_results": 20},
        "set_kpi_protocol": {
            "sample_size_min": 500,
            "baseline_window_days": 30,
            "measurement_method": "rolling_window",
            "error_budget_percent": 2.0,
            "drift_threshold_percent": 2.0,
            "thresholds": defaults["thresholds"],
        },
        "set_project_path": {"path": defaults["path"]},
        "smart_apply_edit": {
            "file_path": smoke_file,
            "search": "x = legacy_helper()",
            "replace": "x = modern_helper()",
        },
        "upsert_scope_owner": {
            "framework": defaults["framework"],
            "owner": defaults["owner"],
            "due_date": defaults["due_date"],
            "done_criteria": defaults["done_criteria"],
            "status": "in_progress",
        },
        "verify_endpoint": {"method": "GET", "url": "/giris"},
        "verify_rn_screen": {"screen_name": defaults["screen_name"]},
        "verify_schema": {"table_or_model": defaults["table_or_model"], "columns": defaults["columns"]},
    }.get(name, {})


def _generic_value_for_required(
    key: str,
    schema_prop: Dict[str, Any],
    defaults: Dict[str, Any],
) -> Any:
    if key in defaults and defaults[key] not in ("", None):
        return defaults[key]

    if "enum" in schema_prop and schema_prop["enum"]:
        return schema_prop["enum"][0]

    t = schema_prop.get("type")
    if t == "string":
        return "smoke"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        item_schema = schema_prop.get("items", {})
        if item_schema.get("type") == "string":
            return ["smoke"]
        if item_schema.get("type") == "object":
            return [{}]
        return []
    if t == "object":
        return {}
    return "smoke"


async def run_matrix(project_path: str, report_file: str) -> Dict[str, Any]:
    defaults = _build_key_defaults(project_path)
    _prepare_smoke_files(project_path)

    server = StdioServerParameters(
        command=str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "blindspot-mcp"),
        args=["--project-path", project_path],
        cwd=project_path,
    )

    started = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    skip_tools = {
        "clear_settings": "global settings reset (intentional skip in matrix)",
    }

    with open(os.devnull, "w", encoding="utf-8") as devnull:
        async with stdio_client(server, errlog=devnull) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=dt.timedelta(seconds=90)) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_map = {t.name: t for t in tools.tools}
                tool_names = sorted(tool_map.keys())

                # Pre-seed IDs required by approval/audit tools.
                request_break_glass = await _call_tool(
                    session,
                    "request_break_glass",
                    _tool_overrides("request_break_glass", defaults),
                )
                payload = request_break_glass.get("payload") or {}
                if payload.get("request_id"):
                    defaults["request_id"] = payload["request_id"]

                request_policy = await _call_tool(
                    session,
                    "request_policy_change",
                    _tool_overrides("request_policy_change", defaults),
                )
                payload = request_policy.get("payload") or {}
                if payload.get("change_id"):
                    defaults["change_id"] = payload["change_id"]

                backup = await _call_tool(session, "create_audit_backup", {})
                payload = backup.get("payload") or {}
                if payload.get("backup_id"):
                    defaults["backup_id"] = payload["backup_id"]

                await _call_tool(session, "create_rollout_plan", {"release_id": defaults["release_id"]})
                policy_eval = await _call_tool(session, "run_policy_evaluation", _tool_overrides("run_policy_evaluation", defaults))
                payload = policy_eval.get("payload") or {}
                if payload.get("run_id"):
                    defaults["run_id"] = payload["run_id"]

                for name in tool_names:
                    if name in skip_tools:
                        rows.append({
                            "tool": name,
                            "status": "skipped",
                            "reason": skip_tools[name],
                        })
                        continue

                    schema = tool_map[name].inputSchema or {}
                    properties: Dict[str, Any] = schema.get("properties", {}) or {}
                    required: List[str] = list(schema.get("required", []) or [])

                    if name in {
                        "apply_edit",
                        "apply_edit_multi",
                        "smart_apply_edit",
                        "safe_fix",
                        "safe_implement",
                        "safe_migrate",
                        "safe_optimize",
                        "safe_refactor",
                        "rename_symbol",
                    }:
                        _reset_smoke_file(project_path)

                    args = {}
                    for key in required:
                        prop_schema = properties.get(key, {})
                        args[key] = _generic_value_for_required(key, prop_schema, defaults)
                    args.update(_tool_overrides(name, defaults))

                    for dyn_key in ("request_id", "change_id", "backup_id", "run_id", "assumption_id"):
                        if dyn_key in args and not args[dyn_key] and defaults.get(dyn_key):
                            args[dyn_key] = defaults[dyn_key]

                    if name == "approve_break_glass" and not defaults.get("request_id"):
                        req = await _call_tool(session, "request_break_glass", _tool_overrides("request_break_glass", defaults))
                        payload = req.get("payload") or {}
                        if payload.get("request_id"):
                            defaults["request_id"] = payload["request_id"]
                        args["request_id"] = defaults.get("request_id", "")

                    if name == "approve_policy_change" and not defaults.get("change_id"):
                        req = await _call_tool(session, "request_policy_change", _tool_overrides("request_policy_change", defaults))
                        payload = req.get("payload") or {}
                        if payload.get("change_id"):
                            defaults["change_id"] = payload["change_id"]
                        args["change_id"] = defaults.get("change_id", "")

                    if name == "get_break_glass_request" and not defaults.get("request_id"):
                        rows.append({"tool": name, "status": "skipped", "reason": "missing request_id"})
                        continue

                    if name == "restore_audit_backup" and not defaults.get("backup_id"):
                        rows.append({"tool": name, "status": "skipped", "reason": "missing backup_id"})
                        continue

                    if name == "replay_session" and not defaults.get("run_id"):
                        rows.append({"tool": name, "status": "skipped", "reason": "missing run_id"})
                        continue

                    if name == "resolve_assumption" and not defaults.get("assumption_id"):
                        ledger = await _call_tool(session, "get_assumption_ledger", {})
                        payload = ledger.get("payload") or {}
                        items = payload.get("assumptions", [])
                        if items:
                            defaults["assumption_id"] = items[0].get("id", "")
                        if not defaults.get("assumption_id"):
                            rows.append({"tool": name, "status": "skipped", "reason": "no assumption in ledger"})
                            continue
                        args["assumption_id"] = defaults["assumption_id"]

                    res = await _call_tool(session, name, args)
                    rows.append({
                        "tool": name,
                        "status": res.get("status"),
                        "ok": res.get("ok", False),
                        "args_used": args,
                        "head": res.get("head", ""),
                        "error": res.get("error", ""),
                    })

    failures = [r for r in rows if r.get("status") in {"error", "exception", "failed"}]
    blocked = [r for r in rows if r.get("status") == "blocked"]
    success_like = {
        "success", "ok", "clean", "ready", "active", "warning", "unknown",
    }
    success = [r for r in rows if r.get("status") in success_like]
    skipped = [r for r in rows if r.get("status") == "skipped"]
    unknown = [r for r in rows if r.get("status") not in success_like | {"blocked", "error", "exception", "failed", "skipped"}]

    report = {
        "status": "success",
        "project_path": project_path,
        "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary": {
            "total_tools_seen": len(rows),
            "success": len(success),
            "blocked": len(blocked),
            "skipped": len(skipped),
            "failed": len(failures),
            "unknown": len(unknown),
        },
        "failures": failures,
        "rows": rows,
    }

    out_path = Path(report_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP tool smoke matrix")
    parser.add_argument(
        "--project-path",
        default="/Users/umuterdal/htdocs/hizmetto-web",
        help="Target project path for MCP",
    )
    parser.add_argument(
        "--report-file",
        default=".blindspot/output/reports/tool_smoke_matrix.json",
        help="Output report JSON path",
    )
    args = parser.parse_args()

    report = anyio.run(run_matrix, args.project_path, args.report_file)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
