# Blindspot MCP — The External Brain for AI Coding Agents

> **See what your LLM can't see.** Early release — Laravel production-tested, 15 more framework plugins in alpha.

**v0.1.5** — [Report bugs](https://github.com/umuterdal/blindspot-mcp/issues) | [Contribute](https://github.com/umuterdal/blindspot-mcp/pulls)

[![PyPI version](https://img.shields.io/pypi/v/blindspot-mcp.svg)](https://pypi.org/project/blindspot-mcp/)
[![PyPI downloads](https://img.shields.io/pypi/dm/blindspot-mcp.svg)](https://pypi.org/project/blindspot-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

### What's New in v0.1.5

- Fail-closed safety orchestration (`safe_implement`, `safe_refactor`, `safe_optimize`, `safe_migrate`, `safe_fix`)
- Language adapter matrix (`run_diff_aware_quality_matrix`) with per-language syntax/static/format/test gates
- Universal completion gate (`run_universal_completion_gate`) and bounded auto-fix loop for post-write verification
- Governance and release controls (policy approvals, break-glass, rollout, backup/DR, release readiness)
- Mandatory release evidence reporting (`conformance_matrix`, `gate_evidence_pack`, `kpi_report`, `open_risk_register`)
- CI safety release gate workflow and expanded test coverage for safety/governance and Laravel route validation

### Install

```bash
pip install blindspot-mcp
```

**Claude Code** — add to `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "blindspot": {
      "command": "blindspot-mcp",
      "args": ["--project-path", "/path/to/your/project"]
    }
  }
}
```

**Cursor / VS Code** — add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "blindspot": {
      "command": "blindspot-mcp",
      "args": ["--project-path", "."]
    }
  }
}
```

---

## Why Blindspot Exists

I built Blindspot because I was frustrated. I use AI coding agents (Claude Code, Cursor) daily on my Laravel project, and they kept making the same mistakes — changing a model field without knowing which controllers use it, editing a route without checking the middleware chain, breaking cache invalidation because they couldn't see the full picture.

**The root cause is simple:** AI agents have limited context windows. They can read 5-10 files, but your project has hundreds. They edit blindly.

So I built an "external brain" — a tool that indexes the entire codebase and gives the AI structured intelligence without reading files into its context window. I used it on my own Laravel project for months, and the difference was night and day. The AI stopped breaking things. It started writing code that actually understood the project.

Now I'm open-sourcing it with support for **16 frameworks** across **12 programming languages**, because every developer using AI coding tools deserves this.

**This is a community project.** The Laravel plugin is battle-tested on a real production codebase. The other framework plugins are architecturally complete but need real-world testing. Your contributions — bug reports, edge case fixes, new framework support — will make this the definitive code intelligence tool for AI agents.

---

## The Problem

Every AI coding agent today works like this:

```
Developer: "Change the is_active field on the User model"

AI Agent: *reads User.php*
AI Agent: *makes the change*
AI Agent: "Done!"

Reality: 14 controllers, 8 templates, 3 cache keys, and 2 form
         validations just broke. The AI had no idea they existed.
```

**Why does this happen?**

```
Your Project:     ~500 files, ~50,000 lines of code
AI Context Window: ~10 files at a time
AI's Visibility:   2% of your codebase
```

The AI is editing with 98% of the codebase invisible. It's like performing surgery blindfolded.

### The Real Cost

Without codebase intelligence, AI agents waste tokens and your time:

```
Typical AI workflow WITHOUT Blindspot:
  1. Read file A to understand structure       (~2,000 tokens)
  2. Read file B to check imports              (~1,500 tokens)
  3. Read file C to understand relationships   (~3,000 tokens)
  4. Read file D to check routes               (~1,000 tokens)
  5. Read file E to check validation           (~2,000 tokens)
  6. Make the edit                             (~500 tokens)
  7. Realize something broke, read file F      (~2,000 tokens)
  8. Fix the broken thing                      (~500 tokens)
  ─────────────────────────────────────────────
  Total: ~12,500 tokens, 8 tool calls, multiple errors

Same task WITH Blindspot:
  1. get_context_for_edit("User.php", "is_active")  (~800 tokens response)
     → Returns: all relationships, affected controllers,
       cache keys, validation rules, template usages,
       risk level, and the symbol's source code
  2. Make the edit with full awareness          (~500 tokens)
  ─────────────────────────────────────────────
  Total: ~1,300 tokens, 2 tool calls, zero errors
```

**That's ~90% fewer tokens and zero broken code.**

---

## The Solution

Blindspot is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that acts as your AI agent's external brain. It:

1. **Indexes your entire codebase** using tree-sitter (12 languages) into a local SQLite database
2. **Understands your framework** — not just syntax, but relationships, routes, schemas, cache keys, middleware chains
3. **Provides structured intelligence** via MCP tools that any AI agent can call
4. **Never sends your code anywhere** — everything runs locally on your machine

```
┌─────────────────────────────────────────────────────────┐
│                    YOUR AI AGENT                         │
│              (Claude Code / Cursor / Copilot)            │
│                                                         │
│  "I need to change User.is_active"                      │
│       │                                                 │
│       ▼                                                 │
│  ┌─────────────────────────────────────────────────┐    │
│  │  get_context_for_edit("User.php", "is_active")  │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
└─────────────────────────┼───────────────────────────────┘
                          │ MCP Protocol
                          ▼
┌─────────────────────────────────────────────────────────┐
│                  BLINDSPOT MCP SERVER                    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ Deep     │  │ Framework│  │ Symbol               │  │
│  │ Index    │  │ Plugin   │  │ Resolver             │  │
│  │ (SQLite) │  │ (Laravel)│  │ (cross-file analysis)│  │
│  └──────────┘  └──────────┘  └──────────────────────┘  │
│                                                         │
│  Response (structured, ~800 tokens):                    │
│  {                                                      │
│    "symbol_code": "public function getIsActiveAttr...", │
│    "class_hierarchy": { "extends": "Model", ... },      │
│    "ripple_effect": {                                   │
│      "risk_level": "high",                              │
│      "affected_files": 14,                              │
│      "controllers": ["UserController", "AdminCtrl"],    │
│      "cache_keys": ["user_active_count"],               │
│      "templates": ["profile.blade.php", ...]            │
│    },                                                   │
│    "impact_summary": { "total_affected": 22 }           │
│  }                                                      │
└─────────────────────────────────────────────────────────┘
```

The AI now knows **everything** about the impact of its change — without reading a single file.

---

## Token & Context Savings

Blindspot dramatically reduces token usage by replacing file reads with structured queries:

```
┌─────────────────────────────────────────────────────────────┐
│              TOKEN USAGE COMPARISON                          │
│                                                             │
│  Task: "Rename is_active to is_enabled on User model"       │
│                                                             │
│  WITHOUT Blindspot:                                         │
│  ████████████████████████████████████████████  ~15,000 tokens│
│  (Read 7+ files to understand dependencies)                 │
│                                                             │
│  WITH Blindspot:                                            │
│  ██████                                       ~2,000 tokens │
│  (1 call: get_context_for_edit + rename_symbol)             │
│                                                             │
│  Savings: ~87%                                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Task: "Understand the full project structure"              │
│                                                             │
│  WITHOUT Blindspot:                                         │
│  ████████████████████████████████████████████████████████████│
│  ~50,000+ tokens (read dozens of files)                     │
│                                                             │
│  WITH Blindspot:                                            │
│  ████                                         ~1,500 tokens │
│  (1 call: get_project_snapshot)                             │
│                                                             │
│  Savings: ~97%                                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Task: "Check what breaks if I change this function"        │
│                                                             │
│  WITHOUT Blindspot:                                         │
│  ████████████████████████████████             ~10,000 tokens │
│  (Read callers, grep across codebase)                       │
│                                                             │
│  WITH Blindspot:                                            │
│  ███                                            ~800 tokens │
│  (1 call: get_ripple_effect)                                │
│                                                             │
│  Savings: ~92%                                              │
└─────────────────────────────────────────────────────────────┘
```

### What This Means in Practice

| Metric | Without Blindspot | With Blindspot |
|--------|------------------|----------------|
| Tokens per edit task | ~12,000-15,000 | ~1,500-2,500 |
| Files read into context | 5-10 | 0 |
| Tool calls per task | 6-10 | 1-3 |
| Broken code from blind edits | Frequent | Rare |
| Time to understand a new codebase | 30+ minutes of reading | 1 call (`get_project_snapshot`) |

---

## Why This Matters Even More for Smaller Models

Blindspot isn't just for Claude Opus or GPT-4. It's a **game-changer for smaller, cheaper, and faster models** that have limited context windows.

```
┌──────────────────────────────────────────────────────────────────┐
│              CONTEXT WINDOW COMPARISON BY MODEL                  │
│                                                                  │
│  Claude Opus 4    ████████████████████████████████████  200K     │
│  GPT-4o           ████████████████████████████          128K     │
│  Claude Sonnet 4  ████████████████████████████████████  200K     │
│  Gemini 2.5 Flash ██████████████████████████████████    1M       │
│  Codex (CLI)      ████████████████████████████          200K     │
│  GPT-4o mini      ████████████████████████████          128K     │
│  Gemini 2.0 Flash ██████████████████████████████████    1M       │
│  DeepSeek V3      ████████████████                      64K      │
│  Llama 4 Scout    ██████████████████████████████████████ 10M     │
│  Qwen 3           ██████████                            32K      │
│  Local LLMs       ████████                              8-32K    │
│                                                                  │
│  Your 500-file project needs:  ~250K tokens to read everything   │
│  Blindspot gives full awareness for:  ~2K tokens per task        │
└──────────────────────────────────────────────────────────────────┘
```

### The Problem Scales With Model Size

| Model Category | Context Window | Without Blindspot | With Blindspot |
|---------------|---------------|-------------------|----------------|
| **Large models** (Claude Opus, GPT-4o) | 128-200K | Can read ~20 files, still misses 90% of project | Full project awareness in ~2K tokens |
| **Medium models** (Gemini Flash, Codex) | 32-128K | Can read ~5-10 files, misses 95%+ | Full project awareness in ~2K tokens |
| **Small/fast models** (GPT-4o mini, DeepSeek) | 32-64K | Can read ~3-5 files, essentially blind | Full project awareness in ~2K tokens |
| **Local models** (Llama, Qwen, Mistral) | 8-32K | Can barely read 1-2 files | Full project awareness in ~2K tokens |

**The smaller the model, the bigger the impact.**

A local Llama model with 8K context can't even read your main controller file (often 500+ lines = 2K+ tokens). But with Blindspot, it gets:
- The full project structure (`get_project_snapshot` = ~1.5K tokens)
- Complete context for any edit (`get_context_for_edit` = ~800 tokens)
- Symbol-level impact analysis (`get_ripple_effect` = ~500 tokens)

**Total: ~2.8K tokens — fits easily in 8K context, with room to spare for the actual edit.**

### This Unlocks New Workflows

```
Without Blindspot:                     With Blindspot:

"Use expensive models for              "Use cheap/fast models for
 everything because only they           everything because Blindspot
 can hold enough context"               provides the context they need"

Claude Opus: $15/M tokens              GPT-4o mini: $0.15/M tokens
→ 100x more expensive                  → Same quality output
→ Slower                               → 10x faster
→ Still misses things                  → Full project awareness
```

Blindspot essentially **decouples code intelligence from model intelligence**. The model doesn't need to be smart enough to hold your entire codebase in memory — Blindspot does that for it.

### Real-World Impact by Model

| Scenario | Model | Without Blindspot | With Blindspot |
|----------|-------|-------------------|----------------|
| Refactor a model field | GPT-4o mini (128K) | Reads 5 files, misses 9 dependencies | Sees all 14 affected files instantly |
| Understand new codebase | DeepSeek V3 (64K) | Reads ~3 files, barely scratches surface | Full project snapshot in 1 call |
| Safe rename across files | Local Llama (8K) | **Impossible** — can't even fit the analysis | `rename_symbol` handles it end-to-end |
| Check before editing | Qwen 3 (32K) | Reads 2 files, guesses the rest | `get_context_for_edit` returns everything |
| Debug production issue | Gemini Flash (1M) | Can read many files but wastes tokens | Targeted `get_ripple_effect` saves 90% tokens |

**Bottom line:** Blindspot makes small models behave like large models, and large models behave like they've memorized your entire codebase.

---

## What Makes Blindspot Different?

There are other code intelligence MCP servers. Here's why Blindspot is different:

| Feature | Generic MCP Servers | Blindspot |
|---------|-------------------|-----------|
| Symbol search | grep-like text search | Structured cross-file references with usage types (import, call, extends, instantiation) |
| Impact analysis | "who calls this function?" | "If I change this, which controllers, templates, cache keys, and validations break?" |
| Framework awareness | None | Deep understanding of 16 frameworks — routes, ORM, templates, middleware, DI |
| Edit safety | None | Syntax check + auto-rollback + anti-pattern detection + ripple effect warnings |
| Project overview | File listing | Compact structured snapshot: classes, hotspots, import graph, metrics |
| Token efficiency | Read full files | Returns only the data needed, structured and compact |

---

## Supported Frameworks (16)

Blindspot auto-detects your framework and loads only the relevant plugin tools:

| Framework | Language | Tools | What It Understands | Status |
|-----------|----------|-------|-------------------|--------|
| **Laravel** | PHP | 13 | Eloquent relationships, Blade templates, routes, migrations, cache maps, validation chains, middleware | Production-tested |
| **Next.js** | TypeScript | 14 | React components, API routes, Prisma schemas, state management (Zustand/Redux), data fetching, middleware | Tested on real projects |
| **NestJS** | TypeScript | 14 | Module graphs, guards, pipes, interceptors, TypeORM/Prisma, DI | Tested on real projects |
| **Django** | Python | 14 | Model relationships, URL maps, template dependencies, DRF serializers, migrations, cache, middleware | Alpha |
| **Spring Boot** | Java/Kotlin | 14 | JPA entities, endpoint maps, Spring Security filters, DI container, cache annotations, Thymeleaf | Alpha |
| **Express.js** | Node.js | 12 | Mongoose/Sequelize/TypeORM models, route maps, middleware chains, validation (Joi/Zod) | Alpha |
| **Go (Gin/Echo/Chi)** | Go | 12 | GORM structs, route maps, interface implementations, middleware, dependency graphs | Alpha |
| **Rails** | Ruby | 13 | ActiveRecord relationships, route maps, ERB/HAML templates, cache, migrations, jobs | Alpha |
| **FastAPI** | Python | 12 | SQLAlchemy + Pydantic models, Alembic migrations, Depends() injection graph, async patterns | Alpha |
| **Vue/Nuxt 3** | TypeScript | 14 | Vue components, composables, Pinia stores, auto-imports, server routes, middleware | Alpha |
| **SvelteKit** | TypeScript | 13 | File-based routing, Svelte stores, load functions, form actions, hooks | Alpha |
| **Flutter** | Dart | 12 | Widget trees, Riverpod/BLoC state, GoRouter routes, model schemas, assets | Alpha |
| **ASP.NET Core** | C# | 13 | EF Core entities, Razor views, DI container, middleware pipeline, validation | Alpha |
| **React Native** | TypeScript | 12 | React Navigation maps, native modules, platform-specific code, StyleSheet analysis | Alpha |
| **Rust (Actix/Axum)** | Rust | 12 | Structs, trait implementations, error handling chains, middleware layers | Alpha |
| **Phoenix** | Elixir | 13 | Ecto schemas, LiveViews, bounded contexts, plug pipelines, HEEx templates | Alpha |

**Status guide:**
- **Production-tested** — Battle-tested on real production codebases for months
- **Tested on real projects** — Verified on multiple real-world projects with real data
- **Alpha** — Architecture and parsing logic complete, needs community testing on diverse projects. Your bug reports and PRs will make these production-ready!

**No framework detected?** The built-in toolset still works. In Blindspot v0.1.5, the server ships with **86 built-in MCP tools**, and framework plugins are loaded based on detected project structure.

---

## Core Tools (86 Built-In) + Safety Orchestration

These tools work on **every project**, regardless of language or framework:

### Project Bootstrap & Runtime
| Tool | What It Does |
|------|-------------|
| `set_project_path` | Sets the MCP session project root. If `--project-path` is not provided via CLI, this should be called first. |
| `get_settings_info` | Returns active project settings and configuration status. |
| `get_policy_status` | Returns the effective fail-closed policy profile (including `policy_hash`). |
| `get_change_risk` | Quickly summarizes risk level for a file/symbol change. |
| `create_temp_directory` / `check_temp_directory` | Creates and validates the temp directory used by index/safety runtime components. |
| `get_file_watcher_status` / `configure_file_watcher` | Manages file watcher status and behavior (debounce, exclude patterns, observer type). |
| `refresh_search_tools` | Re-detects available code search tools (`rg`, `ugrep`, `ag`, `grep`) in the environment. |
| `clear_settings` | Clears cached settings/state (useful for local reset scenarios). |
| `list_audit_backups` | Lists registered safety/audit backup records. |

### Editing & Safety
| Tool | What It Does |
|------|-------------|
| `apply_edit` | Legacy direct edit tool. In strict fail-closed mode this tool is blocked unless `policy.allow_legacy_write=true`. |
| `smart_apply_edit` | Legacy safe edit tool with ripple analysis. In strict fail-closed mode this is also blocked unless `policy.allow_legacy_write=true`. |
| `apply_edit_multi` | Legacy multi-file atomic edit. Blocked in strict mode unless explicitly allowed. |
| `get_edit_region` | Get a specific region of a file with line numbers. Much cheaper than reading the whole file. |
| `diff_preview` | Preview multi-file edits without applying them. Dry-run mode for large refactoring. |

### Fail-Closed Orchestration
| Tool | What It Does |
|------|-------------|
| `compile_spec` | Converts natural-language request into typed goal, constraints, assumptions, risk domains, and targets. |
| `goal_to_patch` | Produces deterministic patch plan (`compile → policy_write → edit → policy_merge → policy_deploy`). |
| `get_runtime_manifest` | Returns deterministic runtime fingerprint + pinned toolchain checks. |
| `list_patch_primitives` | Lists allowed write primitives used by fail-closed autopilot edits. |
| `run_policy_evaluation` | Fail-closed gate for write/merge/deploy with risk + assumption checks. |
| `run_mutation_property_fuzz_suite` | Runs mutation/property/fuzz verification gates and blocks writes when enforced. |
| `run_diff_aware_quality_matrix` | Diff-aware language matrix for syntax/static/format/tests with fail-closed enforcement. |
| `run_universal_completion_gate` | Final gate: syntax clean + static clean + related tests + high-risk ripple=0. |
| `safe_implement` | Main autopilot command: compile + prechecks + transactional edit + audited gates. |
| `safe_refactor` / `safe_optimize` / `safe_migrate` / `safe_fix` | Same audited fail-closed pipeline for each change type. |
| `record_incident_rule` / `list_incident_rules` | Incident memory rules to auto-block known bad patterns from prior failures. |
| `get_assumption_ledger` / `resolve_assumption` | Tracks unresolved assumptions and blocks writes until resolved. |
| `replay_session` | Replays an audited run from hash-chained records for deterministic verification. |
| `conformance_matrix` | Adapter pass/fail matrix for required framework safety methods. |
| `gate_evidence_pack` | Evidence bundle for write/merge/deploy decisions + rollback traces. |
| `kpi_report` | KPI report with thresholds: `>=95`, `>=90`, `<=2`, `0 critical regression`. |
| `open_risk_register` | Lists open assumptions and blocked/failed runs for closure tracking. |

### Governance & Operations
| Tool | What It Does |
|------|-------------|
| `get_scope_inventory` / `upsert_scope_owner` | Adapter scope inventory with owner, due date, done criteria, status. |
| `get_kpi_protocol` / `set_kpi_protocol` | KPI measurement protocol (sample size, baseline window, drift threshold, error budget). |
| `request_policy_change` / `approve_policy_change` / `list_policy_changes` | Multi-approval policy change flow with active policy promotion. |
| `request_break_glass` / `approve_break_glass` / `get_break_glass_request` | Break-glass workflow for critical path interventions. |
| `rotate_signing_key` / `list_key_rotations` | Signing key rotation audit trail (fingerprint based). |
| `create_audit_backup` / `restore_audit_backup` / `run_dr_drill` | Backup/restore + disaster recovery drill workflow. |
| `create_rollout_plan` / `execute_rollout_stage` / `get_rollout_status` | Staged rollout with automatic rollback on smoke failures. |
| `run_security_quality_suite` | Prompt-injection red-team + PII redaction + escalation cap + rollout safety checks. |
| `run_benchmark_harness` / `list_benchmark_runs` | Stratified benchmark runs (N>=2000 target) + historical performance tracking. |
| `release_readiness_report` | Aggregated production gate with all mandatory reports + write/merge/deploy policy-hash consistency check. |

### Release Evidence Pack (Required)
At release close, collect exactly these 4 reports:
1. `conformance_matrix` (all adapter pass/fail)
2. `gate_evidence_pack` (write + merge + deploy evidence)
3. `kpi_report` (threshold compliance)
4. `open_risk_register` (remaining risk + closure target)

`release_readiness_report` returns all of the above in one response.

### Intelligence & Analysis
| Tool | What It Does |
|------|-------------|
| `get_context_for_edit` | **The "external brain."** Call once before editing. Returns: symbol code, class hierarchy, ripple effect, impact summary — everything you need in one call. |
| `get_ripple_effect` | **Symbol-level** impact analysis. "If I change `User.is_active`, what exactly breaks?" Returns affected files by category with risk level. |
| `get_impact_analysis` | **File-level** impact analysis. "If I modify this file, what's affected?" Scans all symbols in the file and finds cross-file references. |
| `find_references` | Find all files referencing a symbol. Returns structured results with usage types: import, static call, method call, instantiation, extends. |
| `get_class_hierarchy` | Full inheritance chain: extends, implements, mixins/traits, extended_by, implemented_by. Works with PHP, Python, TypeScript, Java, Go, Rust, Ruby. |
| `get_project_snapshot` | Compact overview of the entire project (~5KB). Classes, hotspots, import graph, metrics. Use as the first call in every session. |
| `get_file_summary` | Analyze a file's structure without reading it. Returns: classes, functions, methods, imports, line count. |
| `get_symbol_body` | Extract a symbol's full definition. Two modes: full (with code) or compact (metadata only, ~90% fewer tokens). |

### Code Quality
| Tool | What It Does |
|------|-------------|
| `detect_anti_patterns` | Scan for anti-patterns using built-in rules + custom rules from `.blindspot.yaml`. Supports PHP, JS/TS, Python, Go, Rust. |
| `auto_anti_pattern_check` | Compact post-edit check. Call after `apply_edit` to verify no rules were violated. |
| `rename_symbol` | Safe cross-file rename. Word-boundary aware (won't rename partial matches). Dry-run preview + syntax check. |
| `analyze_queries` | Detect N+1 queries, missing indexes, unbounded queries, queries in loops. |
| `check_eager_loading` | Audit for N+1 risks in controllers and views. |
| `detect_cache_conflicts` | Find duplicate cache keys, dead cache, stale risks, pattern conflicts. |

### Search & Index
| Tool | What It Does |
|------|-------------|
| `search_code_advanced` | Full-text code search with pagination. Auto-selects best tool (ripgrep > ag > grep). |
| `find_files` | Find files matching glob patterns using the in-memory index. |
| `build_deep_index` | Build the full symbol index (tree-sitter + SQLite). Run once per session. |
| `refresh_index` | Rebuild file index after git operations or when things seem stale. |
| `get_rebuild_status` | Check if deep index is built and ready. Shows file count, symbol count, languages. |
| `full_audit` | Comprehensive project audit: security (hardcoded secrets, XSS, SQL injection), performance (N+1, unbounded queries), quality (debug statements, TODOs), dead code. |
| `post_edit_checklist` | Language-aware post-edit steps: syntax check, type check, tests, cache clear — per language (PHP, JS/TS, Python, Go, Rust, Ruby, Java). |

### Built-In Tool Inventory (v0.1.5)

Below is the exact built-in tool inventory currently registered in `blindspot/server.py`:

```text
set_project_path, search_code_advanced, find_files, get_file_summary, get_symbol_body, refresh_index, build_deep_index, get_settings_info, create_temp_directory, check_temp_directory, clear_settings, refresh_search_tools, get_file_watcher_status, configure_file_watcher, find_references, get_class_hierarchy, get_impact_analysis, get_ripple_effect, get_project_snapshot, detect_anti_patterns, apply_edit, get_edit_region, apply_edit_multi, analyze_queries, rename_symbol, check_eager_loading, auto_anti_pattern_check, detect_cache_conflicts, diff_preview, full_audit, post_edit_checklist, get_rebuild_status, get_context_for_edit, smart_apply_edit, get_policy_status, compile_spec, goal_to_patch, get_runtime_manifest, list_patch_primitives, run_mutation_property_fuzz_suite, run_diff_aware_quality_matrix, run_universal_completion_gate, record_incident_rule, list_incident_rules, get_assumption_ledger, resolve_assumption, run_policy_evaluation, get_change_risk, verify_schema, detect_transaction_risks, get_domain_rules, generate_test_skeleton, match_view_guards, safe_implement, safe_refactor, safe_optimize, safe_migrate, safe_fix, replay_session, conformance_matrix, gate_evidence_pack, kpi_report, open_risk_register, get_scope_inventory, upsert_scope_owner, get_kpi_protocol, set_kpi_protocol, request_policy_change, approve_policy_change, list_policy_changes, rotate_signing_key, list_key_rotations, run_benchmark_harness, list_benchmark_runs, request_break_glass, approve_break_glass, get_break_glass_request, create_audit_backup, list_audit_backups, restore_audit_backup, run_dr_drill, create_rollout_plan, execute_rollout_stage, get_rollout_status, run_security_quality_suite, release_readiness_report
```

---

## Smart Edit Pipeline

Blindspot doesn't just find problems — it provides a complete safe-edit workflow with session tracking:

```
1. get_context_for_edit(file, symbol)     ← Understand the code + track pipeline
2. safe_implement(feature_spec, ...)        ← Policy-gated transactional edit (recommended)
     ↓
   Returns:
   - policy_write / policy_merge / policy_deploy decisions
   - prechecks: risk, transaction risks, domain rules, schema checks
   - transactional edit result (+ rollback evidence if any gate fails)
   - run_id for replay/audit evidence
     ↓
3. replay_session(run_id) / gate_evidence_pack(run_id) for proof
4. kpi_report() + open_risk_register() for release readiness
5. post_edit_checklist(file) → required post-edit steps
```

Strict profile additionally enforces:
- uncertainty fail-closed (`confidence_score < policy.min_confidence_write` blocks write)
- patch primitive validation (requested primitive must match detected edit primitive)
- deterministic runtime pin check before write/merge/deploy
- incident-memory auto-block for known failure signatures
- mutation/property/fuzz gate pass before transactional edit
- diff-aware language matrix pass before write and after write (`run_diff_aware_quality_matrix`)
- universal completion gate must pass before merge (`run_universal_completion_gate`)
- bounded post-write auto-fix loop for recoverable anti-pattern/debug artifacts

High-speed profile (`execution.profile=fast_path`) adds:
- parallel prechecks with warm cache reuse
- speculative patch candidate scoring/selection before apply
- targeted impacted tests before write (instead of full suite at write stage)
- runtime budget fail-closed (`runtime_budget_seconds`) per safe run

**Session tracking across multiple edits:**
- Ripple items tracked with lifecycle states: `open` → `resolved` → `reopened`
- Re-edit warnings when the same file is edited multiple times
- Pipeline enforcement: blocks edits on high-risk files without prior `get_context_for_edit`
- Feedback overrides: correct ripple classifications that the AI got wrong
- Decision memory: full edit history for the session

**Compact response system:**
- Small results (<2KB) returned inline
- Large results saved to `.blindspot/output/session_{pid}.json`
- Only summary + `detail_file` path returned to context
- AI reads full details only when needed — saves 80-95% context window

---

## Framework-Specific Features (Per Plugin)

Each of the 16 framework plugins includes specialized tools and is validated against this 10-method adapter contract:

| Method | What It Does |
|--------|-------------|
| `verify_schema` | Verify fields exist in schema/model before editing (migrations, Prisma, JPA, GORM, Ecto, etc.) |
| `detect_transaction_risks` | Find missing transactions around multiple DB writes, cache-in-transaction risks |
| `get_domain_rules` | Directory-aware rules: controllers need auth, models need validation, services need error handling |
| `generate_test_skeleton` | Generate framework-appropriate test boilerplate (Jest, pytest, JUnit, Go test, RSpec, ExUnit, etc.) |
| `match_view_guards` | Cross-reference backend auth guards with frontend template/component conditions |
| `contract_replay` | Endpoint contract replay/smoke validation for API compatibility drift |
| `migration_verify` | Migration safety checks (up/down, index impact, dry-run style verification) |
| `cache_consistency` | Read/write/invalidate consistency checks for cache keys |
| `event_flow_verify` | Event dispatch/listener flow verification for breakage detection |
| `ui_regression_smoke` | UI regression smoke checks for view/template surface changes |

---

## Quick Start

### Install

```bash
pip install blindspot-mcp
```

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "blindspot": {
      "command": "blindspot-mcp",
      "args": ["--project-path", "/path/to/your/project"]
    }
  }
}
```

### Cursor / VS Code

Add to `.cursor/mcp.json` or `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "blindspot": {
      "command": "blindspot-mcp",
      "args": ["--project-path", "."]
    }
  }
}
```

### Framework Override

Framework is auto-detected. Override with:

```bash
blindspot-mcp --project-path . --framework nextjs
```

### First Session

Once configured, start your AI agent and it will automatically have access to Blindspot tools. For best results:

1. Agent calls `build_deep_index` (one-time, indexes all symbols)
2. Agent calls `get_project_snapshot` (understand the project structure)
3. Before any edit, agent calls `get_context_for_edit` (get full awareness)
4. Agent uses `safe_implement` (strict fail-closed pipeline)

---

## Configuration

Create `.blindspot.yaml` in your project root:

```yaml
# Language and framework (auto-detected if omitted)
language: typescript
framework: nextjs

# Custom anti-pattern rules
anti_patterns:
  - pattern: "console\\.log\\("
    severity: error
    message: "Remove console.log before commit"
    file_types: [js, ts, tsx]
  - pattern: "debugger"
    severity: error
    message: "Remove debugger statement"
    file_types: [js, ts, tsx]

# Custom syntax checkers
syntax_check:
  typescript: "npx tsc --noEmit --pretty false {file}"
  python: "python -m py_compile {file}"

# Directory mapping for your project structure
scan_dirs:
  models: "src/models"
  controllers: "src/app/api"
  views: "src/app"
  services: "src/services"
  tests: "__tests__"

# Directories to exclude from scanning
exclude_dirs:
  - .next
  - node_modules
  - coverage

# Fail-closed policy (recommended defaults)
policy:
  profile: strict
  allow_legacy_write: false
  escalation_budget:
    per_run: 8
    per_day: 250

# Language adapter matrix (required checks per changed language)
language_adapters:
  hard_block_missing_tools: true
  default_matrix_always: true
  require_format_checks: false
  languages:
    python:
      syntax_command: "python3 -m py_compile {files}"
      static_command: "python3 -m py_compile {files}"
      format_command: "python3 -m py_compile {files}"
      test_command: "python3 -m unittest -v"

# Bounded auto-fix loop for post-write cleanup
auto_fix_loop:
  enabled: true
  max_attempts: 2

# KPI measurement protocol
kpi_protocol:
  sample_size_min: 500
  baseline_window_days: 30
  measurement_method: rolling_window
  error_budget_percent: 2.0
  drift_threshold_percent: 2.0

# Governance defaults
governance:
  required_policy_approvals: 2
  required_break_glass_approvals: 2
  break_glass_default_ttl_minutes: 30
```

See [`examples/`](examples/) for framework-specific configuration templates.

### Complete Safety Config Reference (`.blindspot.yaml`)

The v0.1.5 safety pipeline reads the following keys:

| Key | Type | Purpose |
|-----|------|---------|
| `policy.profile` | `strict \| relaxed` | Strict mode enables fail-closed enforcement for write/merge/deploy gates. |
| `policy.allow_legacy_write` | `bool` | If `false`, legacy write tools (`apply_edit`, `smart_apply_edit`, `apply_edit_multi`) are blocked. |
| `policy.min_confidence_write` | `0.0-1.0` | Minimum confidence threshold before write gates allow execution. |
| `policy.escalation_budget.per_run` | `float` | Max escalation cost allowed per run. |
| `policy.escalation_budget.per_day` | `float` | Max escalation cost allowed per day. |
| `quality_gates.enabled` | `bool` | Enables mutation/property/fuzz quality gate orchestration. |
| `quality_gates.enforce_for_write` | `bool` | If true, failed quality gates block writes. |
| `quality_gates.timeout_seconds` | `int` | Timeout for quality gate command execution. |
| `quality_gates.mutation_command` | `string` | Command for mutation-style checks. |
| `quality_gates.property_command` | `string` | Command for property-based checks. |
| `quality_gates.fuzz_command` | `string` | Command for fuzz checks. |
| `quality_gates.targeted_tests_enabled` | `bool` | Enables impacted test selection in fast path. |
| `quality_gates.targeted_test_command` | `string` | Command template; `{tests}` is substituted with impacted test modules. |
| `quality_gates.max_targeted_tests` | `int` | Upper bound for targeted tests per run. |
| `language_adapters.hard_block_missing_tools` | `bool` | If `true`, missing checker binaries block required matrix checks (fail-closed). |
| `language_adapters.default_matrix_always` | `bool` | If `true`, quality matrix planning is always active for supported changed languages. |
| `language_adapters.require_format_checks` | `bool` | If `true`, format checks become required in the matrix. |
| `language_adapters.languages.<lang>.parser` | `string` | Declares parser strategy label for that language profile. |
| `language_adapters.languages.<lang>.syntax_command` | `string` | Syntax command template (`{file}`, `{files}`, `{paths}`). |
| `language_adapters.languages.<lang>.static_command` | `string` | Static analysis command template. |
| `language_adapters.languages.<lang>.format_command` | `string` | Format check command template. |
| `language_adapters.languages.<lang>.test_command` | `string` | Test command template. |
| `auto_fix_loop.enabled` | `bool` | Enables bounded post-write auto-fix attempts before rollback. |
| `auto_fix_loop.max_attempts` | `int` | Maximum number of auto-fix attempts (0-5). |
| `deterministic_runtime.require_pinned` | `bool` | Enforces runtime pin checks (fail-closed on mismatch). |
| `deterministic_runtime.container_required` | `bool` | Requires runtime image digest for containerized execution. |
| `deterministic_runtime.pins.python_major` | `int` | Required Python major version. |
| `deterministic_runtime.pins.python_minor` | `int` | Required Python minor version. |
| `benchmark.sample_size_target` | `int` | Target sample size for benchmark harness. |
| `benchmark.seed` | `int` | Deterministic sampling seed. |
| `benchmark.stratified` | `bool` | Enables stratified benchmark generation across framework/risk classes. |
| `execution.profile` | `fast_path \| strict_path \| balanced` | Pipeline mode selection. |
| `execution.runtime_budget_seconds` | `int` | Maximum allowed runtime per safe run. |
| `execution.precheck_parallelism` | `int` | Parallel workers used for prechecks. |
| `execution.speculative_variants` | `int` | Number of speculative patch candidates to score. |
| `execution.warm_cache_ttl_seconds` | `int` | TTL for precheck warm-cache hits. |
| `execution.write_quality_mode` | `targeted \| full` | Targeted or full quality mode at write stage. |
| `execution.run_full_quality_after_write` | `bool` | Run full quality suite post-write even in fast path. |
| `kpi_protocol.sample_size_min` | `int` | Minimum run sample size for KPI validity. |
| `kpi_protocol.baseline_window_days` | `int` | Rolling baseline window length. |
| `kpi_protocol.measurement_method` | `string` | KPI measurement strategy label (e.g. `rolling_window`). |
| `kpi_protocol.error_budget_percent` | `float` | Allowed KPI error budget. |
| `kpi_protocol.drift_threshold_percent` | `float` | Allowed KPI drift threshold. |
| `kpi_protocol.allow_bootstrap_if_empty` | `bool` | If true, empty datasets can bootstrap without hard fail. |
| `kpi_protocol.thresholds.gate_pass_rate_min` | `float` | Minimum required gate pass rate. |
| `kpi_protocol.thresholds.first_pass_rate_min` | `float` | Minimum required first-pass success rate. |
| `kpi_protocol.thresholds.rollback_rate_max` | `float` | Maximum rollback rate. |
| `kpi_protocol.thresholds.critical_regressions_max` | `int` | Maximum allowed critical regressions. |
| `governance.required_policy_approvals` | `int` | Number of approvals required for policy changes. |
| `governance.required_break_glass_approvals` | `int` | Number of approvals required for break-glass requests. |
| `governance.break_glass_default_ttl_minutes` | `int` | Default TTL for approved break-glass windows. |

---

## CLI Options

```
blindspot-mcp [options]

Options:
  --project-path PATH     Root directory of the project (recommended; otherwise call set_project_path)
  --framework NAME        Override auto-detected framework
  --transport TYPE        stdio | sse | streamable-http (default: stdio)
  --mount-path PATH       Mount path when using SSE transport
  --port PORT             Port for HTTP transports (default: 8000)
  --indexer-path PATH     Custom path for storing index data
  --tool-prefix PREFIX    Prefix for all tool names
```

---

## How It Works

```
┌──────────────────────────────────────────────────────────┐
│                    INDEXING PHASE                         │
│                                                          │
│  Source Files ──→ Tree-sitter Parser ──→ SQLite Index     │
│  (.py .ts .go     (12 languages)        (symbols, lines, │
│   .java .php                             imports, types) │
│   .rs .rb .dart                                          │
│   .cs .ex)                                               │
│                                                          │
│  Framework Files ──→ Regex Parser ──→ Framework Metadata │
│  (routes, models,   (per plugin)     (relationships,     │
│   templates, config)                  routes, schemas)    │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                    QUERY PHASE                            │
│                                                          │
│  AI Agent calls MCP tool                                 │
│       │                                                  │
│       ▼                                                  │
│  Symbol Resolver ──→ Cross-file analysis ──→ Structured  │
│  (deep index +       (references, hierarchy,  response   │
│   file scanning)      impact, ripple)         (~800 tok) │
│                                                          │
│  Framework Plugin ──→ Domain-specific ──→ Rich context   │
│  (only if detected)   analysis             (routes,      │
│                       (routes, ORM,         schemas,     │
│                        templates)           middleware)   │
└──────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Local only** — Your code never leaves your machine. Everything runs in-process.
- **Framework-aware loading** — Detected framework plugins load automatically. The built-in toolset is 86 tools; plugin-specific extras are only active for relevant projects.
- **Deep index + file scanning** — Tree-sitter for structured symbol data, regex for cross-file references. Best of both worlds.
- **Compact responses** — Every tool is designed to return the minimum data needed. Large diffs get summarized. Symbol bodies can be fetched in compact mode (~90% fewer tokens).

---

## Writing a Plugin

Adding support for a new framework requires two files:

### 1. Intelligence Service (`blindspot/services/myframework_intelligence_service.py`)

```python
from .base_service import BaseService
from typing import Any, Dict, Optional

class MyFrameworkIntelligenceService(BaseService):
    """Intelligence service for MyFramework."""

    def get_model_schema(self, model_name: str = None) -> Dict[str, Any]:
        base = self.base_path
        # Parse your framework's model files with regex
        # Return structured data
        return {"status": "success", "models": [...]}

    def get_route_map(self, filter_prefix: str = None) -> Dict[str, Any]:
        # Parse your framework's route definitions
        return {"status": "success", "routes": [...]}

    # Add 10-12 more methods following the pattern of existing plugins
```

### 2. Plugin Registration (`blindspot/plugins/myframework/__init__.py`)

```python
from ..base_plugin import BlindspotPlugin
from mcp.server.fastmcp import Context, FastMCP

class MyFrameworkPlugin(BlindspotPlugin):
    @property
    def name(self) -> str:
        return "myframework"

    @property
    def framework(self) -> str:
        return "myframework"

    def register_tools(self, mcp: FastMCP) -> None:
        from ...services.myframework_intelligence_service import MyFrameworkIntelligenceService
        from ...utils import handle_mcp_tool_errors
        from ...server import with_concurrency_limit

        @mcp.tool()
        @handle_mcp_tool_errors(return_type="dict")
        @with_concurrency_limit
        def get_myframework_models(ctx: Context, model_name: str = None):
            """Get model schema for MyFramework projects."""
            return MyFrameworkIntelligenceService(ctx).get_model_schema(model_name)
```

Then add it to `blindspot/plugins/__init__.py` in `load_builtin_plugins()`.

Look at `blindspot/plugins/laravel/__init__.py` and `blindspot/services/laravel_intelligence_service.py` for the most complete reference implementation.

---

## Contributing

This project was born from a real need — I built it for my own Laravel project and it transformed how AI agents work with my code. Now it's open source because I believe every developer deserves this.

**The Laravel plugin is battle-tested.** The other 15 framework plugins are architecturally complete with real parsing logic, but they need real-world testing across diverse projects. This is where the community comes in.

### How You Can Help

- **Use it on your project** and report issues. The most valuable contribution is real-world testing.
- **Fix edge cases** in framework plugins. Regex parsers don't cover every syntax variation — your PR fixing a parsing edge case helps everyone.
- **Add new framework plugins.** The plugin architecture makes it straightforward.
- **Improve documentation.** Better examples, tutorials, framework-specific guides.

### Development Setup

```bash
git clone https://github.com/umuterdal/blindspot-mcp.git
cd blindspot-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e "."
```

### Running

```bash
# Test with MCP Inspector (browser UI)
npx @modelcontextprotocol/inspector .venv/bin/blindspot-mcp --project-path /path/to/project

# Or add to your AI agent config and use directly
```

### Release Gate (One Command)

```bash
python scripts/release_gate.py --project-path . --output .blindspot/output/release_readiness.json
```

The command exits non-zero when release readiness fails.

Export mandatory 4 reports as separate files:

```bash
python scripts/export_required_reports.py --project-path . --out-dir .blindspot/output/reports
```

### PyPI Release Checklist (Maintainers)

```bash
# 1) bump version
# edit pyproject.toml -> [project].version

# 2) run tests
python -m unittest discover -s tests -p "test_*.py" -v

# 3) build + validate artifacts
python -m build
python -m twine check dist/*

# 4) publish
python -m twine upload dist/*

# 5) git release
git add pyproject.toml README.md
git commit -m "Bump version to X.Y.Z"
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main --tags
```

Versioning note: package version is `X.Y.Z` in `pyproject.toml`; git tag uses `vX.Y.Z`.

---

## Compatible AI Tools & Models

Blindspot works with any AI tool that supports the Model Context Protocol (MCP):

### AI Coding Tools
| Tool | Support | Configuration |
|------|---------|--------------|
| **Claude Code** (CLI) | Full support | `~/.claude/settings.json` |
| **Cursor** | Full support | `.cursor/mcp.json` |
| **VS Code + Copilot** | Full support | `.vscode/mcp.json` |
| **Windsurf** | Full support | MCP config |
| **Cline** | Full support | MCP config |
| **Continue.dev** | Full support | MCP config |
| **Any MCP client** | Full support | stdio or SSE transport |

### AI Models (All Work With Blindspot)
| Model | Provider | Context | Blindspot Benefit |
|-------|----------|---------|------------------|
| Claude Opus 4 / Sonnet 4 | Anthropic | 200K | Saves 87-97% tokens, prevents blind edits |
| GPT-4o / GPT-4o mini | OpenAI | 128K | Critical for mini — can't hold large projects without Blindspot |
| Gemini 2.5 Pro / Flash | Google | 1M / 1M | Even with huge context, saves tokens and provides structured data vs raw file reads |
| Codex (OpenAI CLI) | OpenAI | 200K | Structured intelligence beats raw file reads |
| DeepSeek V3 / R1 | DeepSeek | 64K | **Essential** — 64K can't hold most projects; Blindspot makes it viable |
| Llama 4 Scout/Maverick | Meta | 10M/1M | Structured context beats brute-force file reading |
| Qwen 3 | Alibaba | 32K | **Essential** — too small for most projects without Blindspot |
| Mistral Large / Codestral | Mistral | 32-128K | Significant benefit, especially for Codestral |
| Local models (Ollama) | Self-hosted | 4-32K | **Game-changer** — makes local models usable for real projects |

**Key insight:** The smaller your model's context window, the more you need Blindspot. But even the largest models benefit because structured data beats raw file reads for accuracy and speed.

---

## Monorepo Support

Blindspot automatically detects monorepo structures and loads plugins for **all** detected frameworks:

```
your-project/
├── frontend/     → Next.js detected → Next.js plugin loaded
├── backend/      → NestJS detected  → NestJS plugin loaded
├── mobile/       → React Native detected → RN plugin loaded
└── package.json  → workspaces: ["frontend", "backend", "mobile"]
```

Just point Blindspot at the **root** — it finds everything:

```json
{
  "mcpServers": {
    "blindspot": {
      "command": "blindspot-mcp",
      "args": ["--project-path", "/path/to/monorepo-root"]
    }
  }
}
```

Supported monorepo tools: npm workspaces, yarn workspaces, pnpm workspaces, Turborepo, Nx, Lerna.

---

## Project Stats

```
Tracked Python files:   123 (blindspot + tests + scripts)
Tracked LOC:            ~79,700
Framework plugins:      16
Built-in MCP tools:     86 (server-registered)
Plugin tools:           Auto-loaded by detected framework(s)
Supported languages:    12 (PHP, TypeScript, JavaScript, Python, Java,
                            Kotlin, Go, Rust, Ruby, C#, Dart, Elixir)
Tree-sitter parsers:    12
Syntax check support:   PHP, JavaScript, TypeScript, Python, Go, Rust
Monorepo support:       Automatic workspace detection
Session tracking:       Audit/replay, assumption ledger, policy gates, rollback evidence
Compact responses:      Large results auto-saved to .blindspot/output/
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

Built with frustration, then with love. If this saves you even one hour of debugging AI-generated code, it was worth it.
