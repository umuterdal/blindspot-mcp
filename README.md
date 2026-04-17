# Blindspot MCP

Blindspot is a local, general-purpose context engine for AI coding agents.

It exists for one job:

- help the agent understand a project
- help the agent understand symbols and relationships
- help the agent estimate change impact before editing
- do this across different languages and project layouts

Blindspot is intentionally small. It is not trying to be a deployment system, policy engine, rollout manager, or autonomous edit platform.

## Why

Most agents can edit files, search text, and run commands.
What they usually lack is a compact, reusable understanding layer for:

- project structure
- symbol ownership
- callers and references
- inheritance and composition
- likely blast radius of a change

That is the gap Blindspot fills.

Paid context engines already proved this model is useful. Blindspot is the local and reusable alternative.

## Design Goals

- Small MCP surface
- Works across languages and frameworks
- Gives structured context instead of file dumps
- Helps any agent write better code with fewer blind edits
- Stays useful even when framework-specific intelligence is missing

## Language Coverage

Blindspot keeps one public API and improves the engine underneath it.

- Strong support: Python, PHP, JavaScript, TypeScript, Go
- Solid project-structure and syntax-aware support: Dart / Flutter, React Native, Node.js
- Additional generic support: Java, Kotlin, C#, Ruby, Rust, mixed repos

The contract does not change per language. `get_context(...)` stays the same; only the quality of the internal enrichment improves.

## Core Tools

Blindspot now exposes only the core context-engine surface:

- `set_project_path`: call once to bind Blindspot to the repo
- `get_project_snapshot`: one-shot repo overview at session start
- `get_context`: main entrypoint for file, symbol, relationship, and impact context
- `get_symbol_body`: exact symbol metadata or bounded source excerpt
- `get_edit_region`: small numbered excerpt around a symbol or line range
- `search_code`: fallback text search when structured context is not enough
- `find_files`: locate candidate files before drilling into one with `get_context`
- `refresh_index`: rebuild the shallow file index if discovery gets stale
- `build_deep_index`: rebuild the deep symbol index for richer relationship analysis

## Main Workflow

1. Call `set_project_path(...)`
2. Call `get_project_snapshot()` once at the start of a session
3. Before any important edit, call `get_context(target=..., intent="before_edit", symbol=...)`
4. If exact source is needed, call `get_symbol_body(...)` or `get_edit_region(...)`
5. If the returned context is still insufficient, use `search_code(...)`

## Tool Selection Rule

Agents should default to `get_context`.

- Use `get_project_snapshot` for orientation
- use `get_context` for understanding and edit planning
- use `get_symbol_body` for one symbol
- use `get_edit_region` for a tight excerpt
- use `find_files` to locate candidates
- use `search_code` only as fallback
- use index rebuild tools only when index data is missing or stale

## The Main Idea

The agent should not decide between 40 different analysis tools.

Instead, it should call one entrypoint and receive a normalized context envelope:

- `project`
- `target`
- `overview`
- `file_context`
- `symbol_context`
- `relationship_context`
- `impact_context`
- `direct_callers`
- `indirect_dependents`
- `blast_radius`
- `risk_reasons`
- `safe_edit_hints`
- `related_files`
- `related_file_reasons`
- `missing_context`
- `confidence`
- `confidence_details`
- `edit_plan`
- `suggested_next_steps`

That is what `get_context(...)` returns.

## Supported Intents

`get_context(...)` supports these intents:

- `project`
- `file`
- `symbol`
- `before_edit`
- `impact`

This keeps the public API small while still covering the main agent workflows.

`get_context(...)` also accepts `change_type`:

- `modify`
- `rename`
- `delete`
- `signature_change`
- `contract_change`

Use a stronger `change_type` when you want Blindspot to plan a coordinated refactor instead of a local edit.

## Install

```bash
pip install blindspot-mcp
```

## Claude Code

Add this to `~/.claude/settings.json`:

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

## Cursor / VS Code

Add this to `.cursor/mcp.json`:

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

## Example

```text
Developer: Change the User status behavior

Agent:
1. get_project_snapshot()
2. get_context(target="app/models/user.py", intent="before_edit", symbol="is_active")
3. get_symbol_body("app/models/user.py", "is_active")
4. edit with full awareness of related files and likely impact
```

## What Blindspot Does Not Try To Be

- a code editor
- a release gate system
- a deployment orchestrator
- a policy approval workflow
- a giant framework tool catalog

Those concerns make the product noisier and make agents choose tools instead of understanding code.

## Local-First

Blindspot runs locally and analyzes the codebase on your machine.

## Current Product Direction

Blindspot should become:

- a stable context layer any AI agent can use
- easy to plug into different editors and agent runtimes
- reliable across Python, PHP, TypeScript, JavaScript, Go, Java, Ruby, Rust, C#, and mixed repos
- reliable across Dart / Flutter, React Native, Node.js, and mixed monorepos
- useful even in plain projects with no heavy framework detection

The standard for success is simple:

- fewer blind edits
- better relationship awareness
- better change impact awareness
- better code written by the agent

## Development

Run the core test suite:

```bash
python3 -m unittest tests.test_context_engine_service -v
```

Run the lightweight context evaluation harness:

```bash
.venv/bin/python evals/run_context_eval.py
```
