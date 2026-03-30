"""
Blindspot MCP Server

This MCP server allows LLMs to index, search, and analyze code from a project directory.
It provides tools for file discovery, content retrieval, and code analysis.

This version uses a service-oriented architecture where MCP decorators delegate
to domain-specific services for business logic.
"""

# Standard library imports
import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

# Third-party imports
from mcp.server.fastmcp import Context, FastMCP

# Local imports
from .project_settings import ProjectSettings
from .services import FileService, FileWatcherService, SearchService, SettingsService, SafetyOrchestrationService
from .services.code_intelligence_service import CodeIntelligenceService
from .services.file_edit_service import FileEditService
from .services.generic_intelligence_service import GenericIntelligenceService
from .services.file_discovery_service import FileDiscoveryService
from .services.index_management_service import IndexManagementService
from .services.project_management_service import ProjectManagementService
from .services.settings_service import manage_temp_directory
from .services.system_management_service import SystemManagementService
from .services.laravel_validation_service import LaravelValidationService
from .services.advanced_analysis_service import AdvancedAnalysisService
from .utils import handle_mcp_tool_errors

# Concurrency control with FIFO queue for fair request ordering
MAX_CONCURRENT_REQUESTS = 5


class FIFOConcurrencyLimiter:
    """
    FIFO queue-based concurrency limiter with timeout.

    Ensures requests are processed in arrival order while limiting
    concurrent executions. Uses a ticket-based system for fairness.
    """

    def __init__(self, max_concurrent: int, timeout: float = 60.0):
        self._max_concurrent = max_concurrent
        self._timeout = timeout
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._active_count = 0
        self._next_ticket = 0
        self._serving_ticket = 0

    def acquire(self, timeout: float = None) -> int:
        """Acquire a slot in FIFO order. Returns ticket number.

        Raises TimeoutError if slot cannot be acquired within timeout.
        """
        timeout = timeout or self._timeout

        with self._condition:
            my_ticket = self._next_ticket
            self._next_ticket += 1

            # Wait until it's our turn AND there's capacity
            start = time.monotonic()

            while self._serving_ticket != my_ticket or self._active_count >= self._max_concurrent:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    # Timeout: always advance serving_ticket to prevent deadlock
                    if self._serving_ticket <= my_ticket:
                        self._serving_ticket = my_ticket + 1
                    self._condition.notify_all()
                    raise TimeoutError(f"Queue timeout after {timeout}s (ticket {my_ticket})")

                self._condition.wait(timeout=min(remaining, 1.0))

            # It's our turn, take the slot
            self._active_count += 1
            self._serving_ticket += 1
            self._condition.notify_all()
            return my_ticket

    def release(self):
        """Release a slot."""
        with self._condition:
            self._active_count -= 1
            self._condition.notify_all()

    @property
    def stats(self) -> dict:
        """Get current queue statistics."""
        with self._lock:
            return {
                "active": self._active_count,
                "max_concurrent": self._max_concurrent,
                "next_ticket": self._next_ticket,
                "serving_ticket": self._serving_ticket,
                "queued": self._next_ticket - self._serving_ticket
            }


_concurrency_limiter = FIFOConcurrencyLimiter(MAX_CONCURRENT_REQUESTS)


# Multi-session stability: Handle SIGINT gracefully
# Claude Code sends SIGINT to existing MCP processes when new sessions start
# We ignore SIGINT to maintain stability for the original session
def _setup_signal_handlers():
    """Setup signal handlers for multi-session stability."""
    def sigint_handler(signum, frame):
        # Log but don't exit - let the MCP server continue serving
        logging.getLogger(__name__).warning(
            "Received SIGINT - ignoring for multi-session stability"
        )

    def sigterm_handler(signum, frame):
        # SIGTERM is a polite termination request - we should honor it
        logging.getLogger(__name__).info(
            "Received SIGTERM - shutting down gracefully"
        )
        sys.exit(0)

    # Windows doesn't have SIGINT the same way, but we handle it anyway
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, sigint_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, sigterm_handler)

# Signal handlers deferred to main() — not on import, to avoid host process side effects


def with_concurrency_limit(func):
    """Decorator to limit concurrent tool executions with FIFO ordering."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            _concurrency_limiter.acquire()
        except TimeoutError as e:
            # Return error dict instead of crashing
            logging.getLogger(__name__).warning("Queue timeout for %s: %s", func.__name__, e)
            return {
                "status": "error",
                "error": "queue_timeout",
                "message": f"Server busy, request queued too long. Please retry. ({e})"
            }
        try:
            return func(*args, **kwargs)
        finally:
            _concurrency_limiter.release()
    return wrapper


# Setup logging — scoped to blindspot namespace only, no root logger override
def setup_indexing_performance_logging():
    """Setup blindspot-scoped logging (stderr only). Does NOT touch root logger."""
    blindspot_logger = logging.getLogger("blindspot")
    if not blindspot_logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(logging.ERROR)
        blindspot_logger.addHandler(stderr_handler)
        blindspot_logger.setLevel(logging.DEBUG)


# Initialize logging (scoped, no host process side effects)
setup_indexing_performance_logging()
logger = logging.getLogger(__name__)


@dataclass
class BlindspotContext:
    """Context for the Blindspot MCP server."""

    base_path: str
    settings: ProjectSettings
    file_count: int = 0
    file_watcher_service: FileWatcherService = None


@dataclass
class _CLIConfig:
    """Holds CLI configuration for bootstrap operations."""

    project_path: str | None = None


class _BootstrapRequestContext:
    """Minimal request context to reuse business services during bootstrap."""

    def __init__(self, lifespan_context: BlindspotContext):
        self.lifespan_context = lifespan_context
        self.session = None
        self.meta = None


_CLI_CONFIG = _CLIConfig()


@asynccontextmanager
async def indexer_lifespan(_server: FastMCP) -> AsyncIterator[BlindspotContext]:
    """Manage the lifecycle of the Code Indexer MCP server."""
    # Don't set a default path, user must explicitly set project path
    base_path = ""  # Empty string to indicate no path is set

    # Initialize settings manager with skip_load=True to skip loading files
    settings = ProjectSettings(base_path, skip_load=True)

    # Initialize context - file watcher will be initialized later when project path is set
    context = BlindspotContext(
        base_path=base_path, settings=settings, file_watcher_service=None
    )

    try:
        # Bootstrap project path when provided via CLI.
        if _CLI_CONFIG.project_path:
            bootstrap_ctx = Context(
                request_context=_BootstrapRequestContext(context), fastmcp=mcp
            )
            try:
                message = ProjectManagementService(bootstrap_ctx).initialize_project(
                    _CLI_CONFIG.project_path
                )
                logger.info("Project initialized from CLI flag: %s", message)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Failed to initialize project from CLI flag: %s", exc)
                raise RuntimeError(
                    f"Failed to initialize project path '{_CLI_CONFIG.project_path}'"
                ) from exc

        # Provide context to the server
        yield context
    finally:
        # Stop file watcher if it was started
        if context.file_watcher_service:
            context.file_watcher_service.stop_monitoring()


_INSTRUCTIONS = """
# Blindspot MCP — The External Brain for AI Coding Agents

This MCP gives you deep understanding of any codebase across 16 frameworks.
Use it to write correct code that doesn't break other parts of the system.

## CRITICAL: Think First, Then Use Tools

MCP tools give you DATA — not DECISIONS. You must THINK about each result before acting.

**WRONG approach** (mechanical):
  Tool says fix_required at L221 → blindly add the missing field → move on

**RIGHT approach** (thoughtful):
  Tool says fix_required at L221 in handlePayment() →
  Read the function with get_symbol_body to understand WHY it exists →
  Understand the business flow →
  THEN decide what the correct fix is

**Rules:**
1. For every HIGH priority item: read the enclosing function with `get_symbol_body` BEFORE fixing it.
   Do NOT fix based on the one-line snippet alone — understand the full function context.
2. For every MEDIUM item: think about whether the redundancy is intentional (defense-in-depth)
   or actually redundant. Not every check_redundancy should be removed.
3. Never apply MCP suggestions mechanically. The tool tells you WHERE to look and WHAT might
   be wrong — but the correct FIX requires understanding the business logic.
4. When writing new code, think about it the same way you would without MCP. Use MCP to VERIFY
   your work, not to REPLACE your thinking.

## MANDATORY Edit Workflow

When editing important files (models, controllers, services, handlers, routes), follow this workflow:

1. **Before editing**: Call `get_context_for_edit(file, symbol)` to understand the code
   - Returns: symbol code, class hierarchy, ripple effect, impact summary — ALL in one call
   - This replaces reading 5-10 files manually
2. **Primary edit path**: Call `safe_implement(feature_spec, ...)` (or safe_refactor/safe_fix/etc.)
   - Policy-gated write/merge/deploy
   - Transactional edit with rollback evidence
   - Full audit trail with `run_id` for replay
3. **Read gate outputs carefully**:
   - `policy_write`, `policy_merge`, `policy_deploy`
   - `prechecks` (risk, transaction, domain rules, schema)
   - `rollback` evidence if any gate blocks
4. **Only if strict policy allows legacy writes**:
   - Use `get_context_for_edit` → `smart_apply_edit` → resolve ripple warnings
5. **After all fixes**:
   - `replay_session(run_id)` and `gate_evidence_pack(run_id)` for proof
   - `post_edit_checklist(file)` for required post-edit steps

## When to Use Which Tool

### Understanding Code (before editing)
- `get_project_snapshot()` → First call in new session, understand full codebase
- `get_context_for_edit(file, symbol)` → Everything needed before editing a file
- `get_symbol_body(file, symbol)` → Read a specific function/class WITHOUT opening the file
- `get_symbol_body(file, symbol, compact=True)` → Metadata only (90% fewer tokens)
- `get_file_summary(file)` → File structure: classes, functions, imports, line count
- `get_class_hierarchy(class)` → Inheritance chain: extends, implements, extended_by
- `find_references(symbol)` → Who uses this symbol? With usage types (import, call, extends)

### Checking Impact (before or after editing)
- `get_ripple_effect(file, symbol)` → "If I change X, what breaks?" with risk level
- `get_impact_analysis(file)` → File-level: all symbols and their cross-file references
- `full_audit(focus)` → Project-wide audit: security, performance, quality, dead_code
- `check_eager_loading(file)` → N+1 query risks
- `analyze_queries(controller, method)` → Query performance issues

### Making Edits
- `safe_implement(feature_spec, ...)` → PRIMARY fail-closed edit pipeline
- `safe_refactor / safe_optimize / safe_migrate / safe_fix` → same pipeline per change type
- `run_policy_evaluation(...)` → check write/merge/deploy gates before attempting edits
- `smart_apply_edit(file, search, replace)` → legacy safe edit (blocked in strict mode by default)
- `apply_edit(file, search, replace)` / `apply_edit_multi(file_edits)` → legacy tools
- `rename_symbol(file, old, new, dry_run=True)` → Cross-file rename (always dry_run first)
- `diff_preview(edits)` → Preview changes without applying

### After Editing
- `post_edit_checklist(file)` → Required steps: syntax check, type check, tests, cache clear
- `auto_anti_pattern_check(file)` → Quick check for rule violations
- `detect_anti_patterns(file)` → Full anti-pattern scan with custom rules from .blindspot.yaml

### Search & Discovery
- `search_code_advanced(pattern)` → Full-text search with regex, pagination
- `find_files(pattern)` → Find files by glob pattern
- `get_rebuild_status()` → Check if deep index is built and ready

### Governance & Release
- `get_scope_inventory()` / `upsert_scope_owner(...)` → Adapter owner + due date + done criteria tracking
- `get_kpi_protocol()` / `set_kpi_protocol(...)` → KPI measurement protocol and thresholds
- `request_policy_change(...)` / `approve_policy_change(...)` → Policy approval workflow
- `request_break_glass(...)` / `approve_break_glass(...)` → Break-glass flow for critical paths
- `create_audit_backup()` / `restore_audit_backup(...)` / `run_dr_drill()` → Backup/restore/DR controls
- `create_rollout_plan(...)` / `execute_rollout_stage(...)` → Staged rollout with rollback on smoke failures
- `run_security_quality_suite()` → Prompt-injection, PII redaction, escalation-cap safety checks
- `release_readiness_report()` → Aggregated release gate report

## Compact Response System

All MCP tools return **compact summaries** to save your context window.
Large results are saved to `.blindspot/output/` as detail files.

**What you see in context:** counts, risk levels, file names, priorities — enough to make decisions.
**What's in detail files:** full code snippets, detailed rationale, complete issue lists.

**When to read detail files:**
- You need the actual code snippet from an affected file
- The compact summary isn't enough to make a decision
- You need the full list of issues from full_audit

**How:** Read the file at the path shown in `detail_file` field of any response.
**When NOT to read:** If the compact summary gives you enough info to proceed, don't waste context.

## Framework-Specific Tools

Framework tools are auto-loaded based on your project type. You'll see additional tools for:
- **Next.js**: get_component_tree, get_api_routes, get_prisma_schema, get_state_management
- **NestJS**: get_nestjs_module_map, get_nestjs_endpoint_map, get_nestjs_guard_map
- **Django**: get_django_relationships, get_django_url_map, get_django_migration_schema
- **Laravel**: get_laravel_relationships, get_route_map, get_blade_dependencies
- **And 12 more frameworks** — each with specialized intelligence tools

Use framework-specific tools when available — they understand your framework's patterns deeply.
"""

# Create the MCP server with lifespan manager
mcp = FastMCP("Blindspot", instructions=_INSTRUCTIONS, lifespan=indexer_lifespan, dependencies=["pathlib"])

# ----- COMPACT RESPONSE HELPER -----


def _compact_response(tool_name: str, result, ctx=None) -> dict:
    """Wrap tool results with compact response system.

    Small results (<2KB estimated) pass through unchanged.
    Large results are saved to .blindspot/output/session_{pid}.json
    and a compact summary with detail_file path is returned.
    """
    import json as _json

    # Pass through non-dict results (strings, lists)
    if not isinstance(result, dict):
        return result

    # Estimate size — rough but fast
    try:
        size_estimate = len(_json.dumps(result, default=str))
    except Exception:
        return result

    # Small results pass through (< 2KB)
    if size_estimate < 2000:
        return result

    # Large results → save to file, return compact summary
    try:
        base_path = ""
        if ctx:
            try:
                lc = ctx.request_context.lifespan_context
                base_path = getattr(lc, "base_path", "") or ""
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        from .services.advanced_analysis_service import AdvancedAnalysisService
        detail_path = AdvancedAnalysisService._save_to_session_file(tool_name, result, base_path)

        # Build compact summary
        compact = {
            "status": result.get("status", "success"),
            "detail_file": detail_path,
        }

        # Preserve key summary fields
        for key in ["file", "file_path", "symbol", "total", "total_issues",
                     "summary", "risk_level", "total_files", "metrics",
                     "total_modules", "total_components", "message"]:
            if key in result:
                val = result[key]
                # Keep small values inline, truncate large ones
                if isinstance(val, (str, int, float, bool)):
                    compact[key] = val
                elif isinstance(val, dict) and len(str(val)) < 500:
                    compact[key] = val
                elif isinstance(val, list):
                    compact[key] = f"{len(val)} items (see detail_file)"

        compact["hint"] = f"Full results saved to detail_file. Read only if you need specifics."
        return compact

    except Exception:
        # If save fails, return original result
        return result


def _legacy_write_guard(ctx: Context, tool_name: str) -> Optional[dict]:
    """Fail-closed gate for legacy write tools when strict policy is enabled."""
    try:
        policy = SafetyOrchestrationService(ctx).get_policy_status()
        if (
            policy.get("status") == "success"
            and str(policy.get("profile", "strict")).lower() == "strict"
            and not bool(policy.get("allow_legacy_write", False))
        ):
            return {
                "status": "blocked",
                "tool": tool_name,
                "message": (
                    f"{tool_name} is disabled in strict fail-closed policy. "
                    "Use safe_implement/safe_refactor/safe_optimize/safe_migrate/safe_fix."
                ),
                "policy_hash": policy.get("policy_hash"),
            }
    except Exception as e:
        return {
            "status": "blocked",
            "tool": tool_name,
            "message": f"Policy check failed (fail-closed): {e}",
        }
    return None


# ----- RESOURCES -----


@mcp.resource("files://{file_path}")
def get_file_content(file_path: str) -> str:
    """Get the content of a specific file."""
    decoded_path = unquote(file_path)
    ctx = mcp.get_context()
    return FileService(ctx).get_file_content(decoded_path)


# ----- TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def set_project_path(path: str, ctx: Context) -> str:
    """Set the base project path for indexing."""
    return ProjectManagementService(ctx).initialize_project(path)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def search_code_advanced(
    pattern: str,
    ctx: Context,
    case_sensitive: bool = True,
    context_lines: int = 0,
    file_pattern: str = None,
    fuzzy: bool = False,
    regex: bool = None,
    start_index: int = 0,
    max_results: int | None = 10,
) -> dict[str, Any]:
    """
Search for code pattern with pagination. Auto-selects best search tool (ugrep/ripgrep/ag/grep).
Supports glob file_pattern (e.g., "*.py"), regex patterns, and fuzzy matching (ugrep only).
"""
    return _compact_response("search_code_advanced", SearchService(ctx).search_code(
        pattern=pattern,
        case_sensitive=case_sensitive,
        context_lines=context_lines,
        file_pattern=file_pattern,
        fuzzy=fuzzy,
        regex=regex,
        start_index=start_index,
        max_results=max_results,
    ), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="list")
def find_files(pattern: str, ctx: Context) -> list[str]:
    """
Find files matching glob pattern using in-memory index.
Supports path patterns (*.py, test_*.js) and filename-only matching (README.md).
"""
    return FileDiscoveryService(ctx).find_files(pattern)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_file_summary(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Get a summary of a specific file, including:
    - Line count
    - Function/class definitions (for supported languages)
    - Import statements
    - Basic complexity metrics
    """
    return _compact_response("get_file_summary", CodeIntelligenceService(ctx).analyze_file(file_path), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_symbol_body(file_path: str, symbol_name: str, ctx: Context, compact: bool = False) -> dict[str, Any]:
    """
    Get the source code body of a specific symbol (function, method, or class).

    Two modes:

    - **Full mode** (default): Returns the complete source code of the symbol.
      Use when you need to understand what the code does.

    - **Compact mode** (compact=True): Returns only metadata — signature, line range,
      callers — WITHOUT the code body. ~90% less tokens.
      Use when you already know what to change and just need line numbers for apply_edit.

    Args:
        file_path: Path to the file containing the symbol
        symbol_name: Name of the symbol to retrieve (e.g., "process_data", "MyClass.my_method")
        compact: If True, skip code body and return only metadata (signature, lines, callers)

    Returns:
        Dictionary with symbol info. Code field omitted in compact mode.
    """
    return _compact_response("get_symbol_body", CodeIntelligenceService(ctx).get_symbol_body(file_path, symbol_name, compact=compact), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def refresh_index(ctx: Context) -> str:
    """
Manually rebuild the project file index. Use after git operations or when index seems stale.
"""
    return IndexManagementService(ctx).rebuild_index()


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
@with_concurrency_limit
def build_deep_index(ctx: Context) -> str:
    """
    Build the deep index (full symbol extraction) for the current project.

    This performs a complete re-index and loads it into memory.
    """
    return IndexManagementService(ctx).rebuild_deep_index()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
def get_settings_info(ctx: Context) -> dict[str, Any]:
    """Get information about the project settings."""
    return SettingsService(ctx).get_settings_info()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
def create_temp_directory() -> dict[str, Any]:
    """Create the temporary directory used for storing index data."""
    return manage_temp_directory("create")


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
def check_temp_directory() -> dict[str, Any]:
    """Check the temporary directory used for storing index data."""
    return manage_temp_directory("check")


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def clear_settings(ctx: Context) -> str:
    """Clear all settings and cached data."""
    return SettingsService(ctx).clear_all_settings()


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def refresh_search_tools(ctx: Context) -> str:
    """
    Manually re-detect the available command-line search tools on the system.
    This is useful if you have installed a new tool (like ripgrep) after starting the server.
    """
    return SearchService(ctx).refresh_search_tools()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
def get_file_watcher_status(ctx: Context) -> dict[str, Any]:
    """Get file watcher service status and statistics."""
    return SystemManagementService(ctx).get_file_watcher_status()


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def configure_file_watcher(
    ctx: Context,
    enabled: bool = None,
    debounce_seconds: float = None,
    additional_exclude_patterns: list = None,
    observer_type: str = None,
) -> str:
    """Configure file watcher service settings.

    Args:
        enabled: Whether to enable file watcher
        debounce_seconds: Debounce time in seconds before triggering rebuild
        additional_exclude_patterns: Additional directory/file patterns to exclude
        observer_type: Observer backend to use. Options:
            - "auto" (default): kqueue on macOS for reliability, platform default elsewhere
            - "kqueue": Force kqueue observer (macOS/BSD)
            - "fsevents": Force FSEvents observer (macOS only, has known reliability issues)
            - "polling": Cross-platform polling fallback (slower but most compatible)
    """
    return SystemManagementService(ctx).configure_file_watcher(
        enabled, debounce_seconds, additional_exclude_patterns, observer_type
    )


# ----- CROSS-FILE INTELLIGENCE TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def find_references(symbol: str, ctx: Context, scope: str = "all",
                    model_context: str = None) -> dict[str, Any]:
    """
    Find all files that reference a symbol (class, method, function, etc.).
    Returns structured results with file paths, line numbers, and usage types
    (import, static_call, method_call, instantiation, extends_or_implements, etc.).

    Args:
        symbol: Name to search for (e.g., "UserService", "process_data", "is_active")
        scope: "all", "controllers", "models", "views", "services", "requests", "migrations"
        model_context: Filter by class/model context to avoid false positives on common names.
    """
    return _compact_response("find_references", GenericIntelligenceService(ctx).find_references(symbol, scope, context_filter=model_context), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_class_hierarchy(class_name: str, ctx: Context) -> dict[str, Any]:
    """
    Get the full inheritance/implementation chain for a class.
    Returns: extends, implements, traits/mixins, extended_by, implemented_by with file locations.
    Works with all OOP languages (PHP, Python, Java, TypeScript, etc.).

    Args:
        class_name: Class name to look up (e.g., "UserController", "BaseService")
    """
    return _compact_response("get_class_hierarchy", GenericIntelligenceService(ctx).get_class_hierarchy(class_name), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_impact_analysis(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Analyze what would be affected if a file is modified.
    Finds all files that reference the given file's classes, interfaces, traits, and enums.
    Essential for safe refactoring — know before you change.

    Args:
        file_path: Relative path to analyze (e.g., "src/models/User.py")
    """
    return _compact_response("get_impact_analysis", GenericIntelligenceService(ctx).get_impact_analysis(file_path), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_ripple_effect(file_path: str, symbol: str, ctx: Context,
                      change_type: str = "modify") -> dict[str, Any]:
    """
    Trace the FULL ripple effect of changing a specific symbol in a file.
    Unlike get_impact_analysis (file-level), this is SYMBOL-level:
    "If I change User.is_active, what exactly breaks?"

    Returns categorized impacts with risk_level (low/medium/high/critical).

    Args:
        file_path: File containing the symbol
        symbol: Symbol name (e.g., "is_active", "process_data")
        change_type: "modify" (behavior change), "rename", or "delete"
    """
    return _compact_response("get_ripple_effect", GenericIntelligenceService(ctx).get_ripple_effect(file_path, symbol, change_type), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_project_snapshot(ctx: Context) -> dict[str, Any]:
    """
    Generate a compact snapshot of the ENTIRE project structure (~5KB).
    The "project brain" — use as the FIRST call in every new session.

    Returns:
    - metrics: file counts by language/type
    - models/classes: with relationships, scopes, methods
    - controllers/handlers: with methods, imported classes
    - services: service classes with method counts
    - hotspots: largest/most complex files
    - cross_references: class → consumer connection map
    """
    return _compact_response("get_project_snapshot", GenericIntelligenceService(ctx).get_project_snapshot(), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def detect_anti_patterns(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Scan a file for anti-patterns using built-in rules + custom rules from .blindspot.yaml.
    Supports PHP, JavaScript/TypeScript, Python, Go, Rust, and Blade templates.

    Args:
        file_path: Relative path to the file to check
    """
    return LaravelValidationService(ctx).detect_anti_patterns(file_path)


# ----- FILE EDIT TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def apply_edit(
    file_path: str,
    ctx: Context,
    search: str = None,
    replace: str = None,
    symbol: str = None,
    new_code: str = None,
    edits: list = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
) -> dict[str, Any]:
    """
    Apply edit(s) to a file WITHOUT reading it into context.
    File is read/written server-side. Small diffs returned in full, large diffs return summary only.
    Files are syntax-checked after edit (PHP, JS/TS, Python, Go, Rust) — invalid syntax auto-rolls back.

    Five modes (use exactly one):

    1. Search-replace mode (search + replace):
       Find the exact `search` string and replace with `replace`. Must be unique in the file
       unless `occurrence` is specified.

    2. Search-replace with occurrence (search + replace + occurrence):
       Replace the Nth occurrence of `search` (1-indexed). Use when search string appears multiple times.

    3. Symbol mode (symbol + new_code):
       Find the function/method/class by name via the code index,
       replace its entire body with `new_code`.

    4. Line-range mode (start_line + end_line + new_code):
       Replace lines start_line through end_line (1-indexed, inclusive) with new_code.
       Use when you know exact line numbers from get_edit_region.

    5. Batch mode (edits):
       Multiple search-replace pairs in one call. Each pair: {"search": "...", "replace": "..."}.
       All applied to the same file atomically. Each search must be unique.
       Use this when making 2+ changes to the same file — saves tool calls and context.

    Args:
        file_path: Relative path to the file
        search: [Mode 1/2] Exact string to find
        replace: [Mode 1/2] String to replace with
        symbol: [Mode 3] Symbol name from the index
        new_code: [Mode 3/4] Complete new code for the symbol or line range
        edits: [Mode 5] List of {search, replace} pairs for batch editing
        start_line: [Mode 4] Start line number (1-indexed)
        end_line: [Mode 4] End line number (1-indexed, inclusive)
        occurrence: [Mode 2] Which occurrence to replace (1-indexed)

    Returns:
        Dictionary with status and either compact diff or summary (for large changes).
    """
    blocked = _legacy_write_guard(ctx, "apply_edit")
    if blocked:
        return blocked

    result = FileEditService(ctx).apply_edit(
        file_path=file_path,
        search=search,
        replace=replace,
        symbol=symbol,
        new_code=new_code,
        edits=edits,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
    )

    # Auto-ripple guard: if editing a file with classes/symbols in the deep index,
    # add a warning nudging toward smart_apply_edit for safer editing
    if isinstance(result, dict) and result.get("status") == "success":
        try:
            from .indexing import get_index_manager
            mgr = get_index_manager()
            summary = mgr.get_file_summary(file_path) if mgr else None
            if summary:
                symbol_count = (
                    len(summary.get("classes", [])) +
                    len(summary.get("functions", [])) +
                    len(summary.get("methods", []))
                )
                if symbol_count > 3:
                    result["ripple_hint"] = (
                        f"This file has {symbol_count} symbols. Consider using smart_apply_edit "
                        f"instead of apply_edit for automatic ripple effect analysis."
                    )
        except Exception:
            pass

    return result


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_edit_region(
    file_path: str,
    ctx: Context,
    symbol: str = None,
    start_line: int = None,
    end_line: int = None,
    context_lines: int = 5,
) -> dict[str, Any]:
    """
    Get a specific region of a file with line numbers — much cheaper than reading the whole file.
    Use this to see the exact code before crafting a search-replace edit.

    Two modes:

    1. Symbol mode: provide `symbol` — returns the symbol's code + surrounding lines
    2. Line mode: provide `start_line` and `end_line` — returns that range + context

    Args:
        file_path: Relative path to the file
        symbol: Symbol name to look up (function/method/class)
        start_line: Start line number (1-indexed)
        end_line: End line number (1-indexed)
        context_lines: Number of extra lines above/below the target (default: 5)

    Returns:
        Dictionary with the code region, line numbers, and file metadata.
    """
    return FileEditService(ctx).get_edit_region(
        file_path=file_path,
        symbol=symbol,
        start_line=start_line,
        end_line=end_line,
        context_lines=context_lines,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def apply_edit_multi(
    file_edits: list,
    ctx: Context,
) -> dict[str, Any]:
    """
    Apply edits to multiple files in a single tool call.
    Each item: {"file_path": "path/to/file", "edits": [{"search": "old", "replace": "new"}, ...]}.
    All files validated first — if any fails, no changes are made.
    PHP files are syntax-checked; invalid syntax auto-rolls back that file only.

    Use this for cross-file refactoring operations that would otherwise require 3+ apply_edit calls.

    Args:
        file_edits: List of file edit specifications. Each: {"file_path": str, "edits": [{"search": str, "replace": str}]}

    Returns:
        Dictionary with per-file results and overall status.
    """
    blocked = _legacy_write_guard(ctx, "apply_edit_multi")
    if blocked:
        return blocked

    return FileEditService(ctx).apply_edit_multi(file_edits=file_edits)


# ----- ADVANCED ANALYSIS TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def analyze_queries(controller: str, ctx: Context, method: str = None) -> dict[str, Any]:
    """
    Analyze ORM queries in a controller for performance issues.

    Detects:
    - N+1 query risks (relationship access without eager loading)
    - Missing database indexes on filtered/sorted columns
    - Unbounded queries without pagination on list endpoints
    - Queries inside loops
    - Missing column selection (fetching all columns unnecessarily)

    Cross-references schema to verify index existence.

    Args:
        controller: Controller name (e.g., "UserController" or "Admin/OrderController")
        method: Optional method name. If omitted, analyzes ALL public methods.
    """
    return _compact_response("analyze_queries", AdvancedAnalysisService(ctx).analyze_queries(controller, method), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def rename_symbol(file_path: str, old_name: str, new_name: str, ctx: Context,
                  dry_run: bool = True) -> dict[str, Any]:
    """
    Find all references to a symbol and generate/apply rename edits across files.
    Word-boundary safe — won't rename partial matches.

    Uses find_references to locate all usages, then generates search-replace
    pairs for each affected file. Files are syntax-checked after rename.

    Default is dry_run=True (preview only). Set dry_run=False to apply.

    Args:
        file_path: File containing the symbol (e.g., "src/models/User.py")
        old_name: Current symbol name (e.g., "is_active", "process_data")
        new_name: New symbol name (e.g., "is_enabled", "handle_data")
        dry_run: If True (default), returns planned edits without applying.
                 If False, applies all edits with syntax check + rollback.
    """
    return _compact_response("rename_symbol", AdvancedAnalysisService(ctx).rename_symbol(file_path, old_name, new_name, dry_run), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def check_eager_loading(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Audit a controller or Blade view for N+1 query risks.

    Scans for relationship property access ($model->relationship) and
    cross-references with the query's with() eager loading calls.
    Also checks Blade views rendered by the controller.

    Reports any relationship accessed but not eager-loaded, with fix suggestions.

    Args:
        file_path: Relative path to controller or Blade file
                   (e.g., "app/Http/Controllers/Public/ListingController.php")
    """
    return _compact_response("check_eager_loading", AdvancedAnalysisService(ctx).check_eager_loading(file_path), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def auto_anti_pattern_check(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Run anti-pattern detection on a file (compact format for post-edit use).

    Designed to be called after apply_edit to automatically verify the edited
    file doesn't violate project rules. Returns compact output:
    - "clean" status if no issues
    - Errors and warnings with line numbers and fix suggestions if issues found

    Args:
        file_path: Relative path of the file that was just edited
    """
    return AdvancedAnalysisService(ctx).auto_anti_pattern_check(file_path)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def detect_cache_conflicts(ctx: Context, cache_key: str = None) -> dict[str, Any]:
    """
    Detect cache key conflicts and inconsistencies across the project.

    Scans all models and services for cache operations and finds:
    - Duplicate cache keys invalidated by multiple unrelated models
    - Dead cache: keys invalidated but never read anywhere
    - Stale risk: keys read but never explicitly invalidated
    - Pattern conflicts: wildcard keys that overlap with static keys

    Args:
        cache_key: Optional specific cache key pattern to check.
                   If omitted, performs full audit of all cache keys.
    """
    return _compact_response("detect_cache_conflicts", AdvancedAnalysisService(ctx).detect_cache_conflicts(cache_key), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def diff_preview(edits: list, ctx: Context) -> dict[str, Any]:
    """
    Preview multi-file edits without applying them (dry-run mode).

    Takes a list of planned search-replace edits and shows unified diffs
    for each file, without modifying anything. Use before apply_edit_multi
    for large refactoring operations.

    Args:
        edits: List of edit specifications. Each item:
            {"file_path": "path/to/file", "search": "text to find", "replace": "replacement"}

    Returns:
        Per-file diffs with addition/deletion counts and summary statistics.
    """
    return _compact_response("diff_preview", AdvancedAnalysisService(ctx).diff_preview(edits), ctx)


# ----- AUDIT & CHECKLIST TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def full_audit(ctx: Context, focus: str = "all") -> dict[str, Any]:
    """
    Run comprehensive, language-agnostic project audit.

    Scans all source files for issues across four categories:
    - security: hardcoded secrets, SQL injection, mass assignment, XSS
    - performance: queries in loops, unbounded queries, N+1 patterns
    - quality: debug statements, TODO/FIXME, empty catch blocks, unused imports
    - dead_code: functions/methods with no external references

    Uses the LanguageSyntax adapter for language-specific patterns and the
    deep index for dead code cross-referencing.

    Args:
        focus: Category to scan — "all", "security", "performance", "quality", or "dead_code"
    """
    return AdvancedAnalysisService(ctx).full_audit(focus)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def post_edit_checklist(file_path: str, ctx: Context) -> dict[str, Any]:
    """
    Get a language-aware checklist of steps to take after editing a file.

    Based on file extension and project type, returns required and
    recommended next steps:
    - PHP: syntax check, route cache clear, test run
    - JS/TS: type check, test run (Jest/Vitest)
    - Python: compile check, pytest
    - Go: go vet, go test
    - Rust: cargo check, cargo test
    - Config files: restart server, clear cache
    - Migration files: run migration
    - Docker files: rebuild container
    - Test files: run the specific test

    Args:
        file_path: Relative path to the file that was just edited
    """
    return AdvancedAnalysisService(ctx).post_edit_checklist(file_path)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
def get_rebuild_status(ctx: Context) -> dict[str, Any]:
    """
    Get the current status of the deep index.

    Returns whether the index is built, file count, symbol count,
    and available languages. Useful to check if build_deep_index
    needs to be called before using symbol-dependent tools.
    """
    return IndexManagementService(ctx).get_rebuild_status()


# ----- PROACTIVE CONTEXT TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_context_for_edit(file_path: str, ctx: Context, symbol: str = None) -> dict[str, Any]:
    """
    Auto-gather ALL context needed before editing a file — the "external brain".

    Call this ONCE before editing and get everything needed to write correct code
    without reading any files into your context window:

    - Class/Model: relationships, hierarchy, affected consumers
    - Controller/Handler: routes, middleware, validation, rendered views
    - Service: which consumers call it, dependencies
    - Template: layout, controller, passed variables, components
    - Any file: class hierarchy, who imports this

    If symbol is provided, also includes ripple effect (risk level + affected files).

    Args:
        file_path: Relative path to the file about to be edited
        symbol: Optional method/property to focus on (triggers ripple analysis)
    """
    # Track pipeline call in project-scoped session
    from .services.advanced_analysis_service import _get_session
    try:
        lc = ctx.request_context.lifespan_context
        base = getattr(lc, "base_path", "") or ""
    except Exception:
        base = ""
    session = _get_session(base)
    if file_path not in session["pipeline_calls"]:
        session["pipeline_calls"][file_path] = set()
    session["pipeline_calls"][file_path].add("context")

    return _compact_response("get_context_for_edit", GenericIntelligenceService(ctx).get_context_for_edit(file_path, symbol), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def smart_apply_edit(
    file_path: str,
    ctx: Context,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    pipeline_context: dict = None,
    resolved_items: list = None,
    test_results: list = None,
    strict_mode: dict = None,
    feedback: dict = None,
) -> dict[str, Any]:
    """
    apply_edit with automatic ripple effect analysis — the safe edit tool.

    Does everything apply_edit does (syntax check + rollback + anti-pattern check)
    PLUS automatically detects changed symbols and runs ripple effect on them.

    If a changed symbol affects other files, returns ripple_warnings with:
    - risk_level (low/medium/high/critical)
    - files that need checking
    - cache keys that might need invalidation

    Use this instead of apply_edit for model/controller/service edits.
    For simple template/config edits, regular apply_edit is fine.

    Args:
        Same as apply_edit — all 5 modes (search-replace, batch, symbol, line-range, occurrence).
        pipeline_context: Optional context from get_context_for_edit for pipeline tracking.
        resolved_items: List of ripple item IDs previously resolved by the agent.
        test_results: List of test results to auto-resolve ripple items (e.g., [{"test": "...", "passed": True, "ripple_ids": [...]}]).
        strict_mode: Dict with enforcement options (e.g., {"enforce_pipeline": True}).
        feedback: Dict mapping ripple_id -> {correct, note, original_action} for human overrides.
    """
    blocked = _legacy_write_guard(ctx, "smart_apply_edit")
    if blocked:
        return blocked

    return AdvancedAnalysisService(ctx).smart_apply_edit(
        file_path=file_path,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        pipeline_context=pipeline_context,
        resolved_items=resolved_items,
        test_results=test_results,
        strict_mode=strict_mode,
        feedback=feedback,
    )


# ----- SAFETY ORCHESTRATION TOOLS -----


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_policy_status(ctx: Context) -> dict[str, Any]:
    """Get effective fail-closed policy settings and policy hash."""
    return SafetyOrchestrationService(ctx).get_policy_status()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def compile_spec(
    feature_spec: str,
    ctx: Context,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
) -> dict[str, Any]:
    """Compile natural language feature request into typed goal/constraints/risk spec."""
    return SafetyOrchestrationService(ctx).compile_spec(
        feature_spec=feature_spec,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def goal_to_patch(
    feature_spec: str,
    ctx: Context,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
) -> dict[str, Any]:
    """Convert goal into fail-closed patch plan with stages, targets, and risk domains."""
    return SafetyOrchestrationService(ctx).goal_to_patch(
        feature_spec=feature_spec,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_runtime_manifest(ctx: Context) -> dict[str, Any]:
    """Get deterministic runtime manifest + pin checks."""
    return SafetyOrchestrationService(ctx).get_runtime_manifest()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_patch_primitives(ctx: Context) -> dict[str, Any]:
    """List supported patch primitives for fail-closed safe pipelines."""
    return SafetyOrchestrationService(ctx).list_patch_primitives()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_mutation_property_fuzz_suite(
    ctx: Context,
    target_files: list = None,
    enforce: bool = True,
) -> dict[str, Any]:
    """Run mutation/property/fuzz quality gates (fail-closed when enforce=true)."""
    return _compact_response(
        "run_mutation_property_fuzz_suite",
        SafetyOrchestrationService(ctx).run_mutation_property_fuzz_suite(
            target_files=target_files,
            enforce=enforce,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_diff_aware_quality_matrix(
    ctx: Context,
    target_files: list = None,
    enforce: bool = True,
    stage: str = "write",
) -> dict[str, Any]:
    """Diff-aware quality matrix by language (syntax/static/format/tests)."""
    return _compact_response(
        "run_diff_aware_quality_matrix",
        SafetyOrchestrationService(ctx).run_diff_aware_quality_matrix(
            target_files=target_files,
            enforce=enforce,
            stage=stage,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_universal_completion_gate(
    target_files: list,
    quality_matrix: dict,
    targeted_tests: dict,
    ctx: Context,
    symbol: str = None,
    edit_result: dict = None,
    enforce: bool = True,
) -> dict[str, Any]:
    """Universal completion gate: syntax+static+tests+high-risk ripple=0."""
    return _compact_response(
        "run_universal_completion_gate",
        SafetyOrchestrationService(ctx).run_universal_completion_gate(
            target_files=target_files or [],
            quality_matrix=quality_matrix or {},
            targeted_tests=targeted_tests or {},
            symbol=symbol,
            edit_result=edit_result or {},
            enforce=enforce,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def record_incident_rule(
    name: str,
    pattern: str,
    ctx: Context,
    scope: str = "global",
    severity: str = "high",
    action: str = "block",
    active: bool = True,
    note: str = "",
) -> dict[str, Any]:
    """Create incident-memory rule used to block repeated failure patterns."""
    return SafetyOrchestrationService(ctx).record_incident_rule(
        name=name,
        pattern=pattern,
        scope=scope,
        severity=severity,
        action=action,
        active=active,
        note=note,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_incident_rules(ctx: Context, active_only: bool = True, limit: int = 200) -> dict[str, Any]:
    """List incident-memory rules."""
    return _compact_response(
        "list_incident_rules",
        SafetyOrchestrationService(ctx).list_incident_rules(active_only=active_only, limit=limit),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_assumption_ledger(ctx: Context, run_id: str = None) -> dict[str, Any]:
    """List assumption ledger entries globally or for a specific run."""
    return _compact_response(
        "get_assumption_ledger",
        SafetyOrchestrationService(ctx).get_assumption_ledger(run_id=run_id),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def resolve_assumption(
    assumption_id: str,
    status: str,
    ctx: Context,
    evidence: str = None,
    note: str = None,
) -> dict[str, Any]:
    """Resolve assumption ledger item (verified/rejected/resolved/open)."""
    return SafetyOrchestrationService(ctx).resolve_assumption(
        assumption_id=assumption_id,
        status=status,
        evidence=evidence,
        note=note,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_policy_evaluation(
    feature_spec: str,
    ctx: Context,
    stage: str = "write",
    target_file: str = None,
    target_files: list = None,
    run_id: str = None,
    risk_domains: list = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
) -> dict[str, Any]:
    """Evaluate strict policy gate for write/merge/deploy stages."""
    return SafetyOrchestrationService(ctx).run_policy_evaluation(
        feature_spec=feature_spec,
        stage=stage,
        target_file=target_file,
        target_files=target_files,
        run_id=run_id,
        risk_domains=risk_domains,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_change_risk(file_path: str, ctx: Context, symbol: str = None) -> dict[str, Any]:
    """Compute symbol/file-level ripple risk and critical-path classification."""
    return SafetyOrchestrationService(ctx).get_change_risk(file_path=file_path, symbol=symbol)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def verify_schema(table_or_model: str, columns: list, ctx: Context) -> dict[str, Any]:
    """Framework-agnostic schema verification (column/field existence)."""
    return SafetyOrchestrationService(ctx).verify_schema(table_or_model, columns)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def detect_transaction_risks(file_path: str, ctx: Context) -> dict[str, Any]:
    """Detect transaction boundary and consistency risks in a file."""
    return _compact_response(
        "detect_transaction_risks",
        SafetyOrchestrationService(ctx).detect_transaction_risks(file_path),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_domain_rules(file_path: str, ctx: Context) -> dict[str, Any]:
    """Extract framework/domain-specific business rules from target file context."""
    return _compact_response("get_domain_rules", SafetyOrchestrationService(ctx).get_domain_rules(file_path), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def generate_test_skeleton(file_path: str, symbol: str, ctx: Context) -> dict[str, Any]:
    """Generate framework-aware test skeleton for changed symbol."""
    return SafetyOrchestrationService(ctx).generate_test_skeleton(file_path, symbol)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def match_view_guards(file_path: str, symbol: str, ctx: Context) -> dict[str, Any]:
    """Detect view/auth guard mismatches for rendered paths."""
    return _compact_response("match_view_guards", SafetyOrchestrationService(ctx).match_view_guards(file_path, symbol), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def safe_implement(
    feature_spec: str,
    ctx: Context,
    target_file: str = None,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    file_edits: list = None,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
    expected_schema_fields: list = None,
    schema_entity: str = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
    patch_primitive: str = None,
    execution_profile: str = None,
    runtime_budget_seconds: int = None,
    release_id: str = None,
    deploy_smoke_commands: list = None,
    auto_rollback_deploy: bool = True,
) -> dict[str, Any]:
    """Fail-closed autopilot: compile spec -> policy gates -> transactional edit -> audited replay."""
    result = SafetyOrchestrationService(ctx).safe_implement(
        feature_spec=feature_spec,
        action="implement",
        target_file=target_file,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        file_edits=file_edits,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
        expected_schema_fields=expected_schema_fields,
        schema_entity=schema_entity,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
        patch_primitive=patch_primitive,
        execution_profile=execution_profile,
        runtime_budget_seconds=runtime_budget_seconds,
        release_id=release_id,
        deploy_smoke_commands=deploy_smoke_commands,
        auto_rollback_deploy=auto_rollback_deploy,
    )
    return _compact_response("safe_implement", result, ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def safe_refactor(
    feature_spec: str,
    ctx: Context,
    target_file: str = None,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    file_edits: list = None,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
    expected_schema_fields: list = None,
    schema_entity: str = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
    patch_primitive: str = None,
    execution_profile: str = None,
    runtime_budget_seconds: int = None,
    release_id: str = None,
    deploy_smoke_commands: list = None,
    auto_rollback_deploy: bool = True,
) -> dict[str, Any]:
    """Fail-closed refactor pipeline with audit/replay evidence."""
    result = SafetyOrchestrationService(ctx).safe_refactor(
        feature_spec=feature_spec,
        target_file=target_file,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        file_edits=file_edits,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
        expected_schema_fields=expected_schema_fields,
        schema_entity=schema_entity,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
        patch_primitive=patch_primitive,
        execution_profile=execution_profile,
        runtime_budget_seconds=runtime_budget_seconds,
        release_id=release_id,
        deploy_smoke_commands=deploy_smoke_commands,
        auto_rollback_deploy=auto_rollback_deploy,
    )
    return _compact_response("safe_refactor", result, ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def safe_optimize(
    feature_spec: str,
    ctx: Context,
    target_file: str = None,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    file_edits: list = None,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
    expected_schema_fields: list = None,
    schema_entity: str = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
    patch_primitive: str = None,
    execution_profile: str = None,
    runtime_budget_seconds: int = None,
    release_id: str = None,
    deploy_smoke_commands: list = None,
    auto_rollback_deploy: bool = True,
) -> dict[str, Any]:
    """Fail-closed optimization pipeline with audit/replay evidence."""
    result = SafetyOrchestrationService(ctx).safe_optimize(
        feature_spec=feature_spec,
        target_file=target_file,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        file_edits=file_edits,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
        expected_schema_fields=expected_schema_fields,
        schema_entity=schema_entity,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
        patch_primitive=patch_primitive,
        execution_profile=execution_profile,
        runtime_budget_seconds=runtime_budget_seconds,
        release_id=release_id,
        deploy_smoke_commands=deploy_smoke_commands,
        auto_rollback_deploy=auto_rollback_deploy,
    )
    return _compact_response("safe_optimize", result, ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def safe_migrate(
    feature_spec: str,
    ctx: Context,
    target_file: str = None,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    file_edits: list = None,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
    expected_schema_fields: list = None,
    schema_entity: str = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
    patch_primitive: str = None,
    execution_profile: str = None,
    runtime_budget_seconds: int = None,
    release_id: str = None,
    deploy_smoke_commands: list = None,
    auto_rollback_deploy: bool = True,
) -> dict[str, Any]:
    """Fail-closed migration pipeline with policy-gated safety checks."""
    result = SafetyOrchestrationService(ctx).safe_migrate(
        feature_spec=feature_spec,
        target_file=target_file,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        file_edits=file_edits,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
        expected_schema_fields=expected_schema_fields,
        schema_entity=schema_entity,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
        patch_primitive=patch_primitive,
        execution_profile=execution_profile,
        runtime_budget_seconds=runtime_budget_seconds,
        release_id=release_id,
        deploy_smoke_commands=deploy_smoke_commands,
        auto_rollback_deploy=auto_rollback_deploy,
    )
    return _compact_response("safe_migrate", result, ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def safe_fix(
    feature_spec: str,
    ctx: Context,
    target_file: str = None,
    search: str = None,
    replace: str = None,
    edits: list = None,
    symbol: str = None,
    new_code: str = None,
    start_line: int = None,
    end_line: int = None,
    occurrence: int = None,
    file_edits: list = None,
    constraints: list = None,
    acceptance_criteria: list = None,
    risk_domains: list = None,
    expected_schema_fields: list = None,
    schema_entity: str = None,
    override_token: str = None,
    break_glass_token: str = None,
    estimated_escalation_cost: float = 0.0,
    confidence_score: float = None,
    patch_primitive: str = None,
    execution_profile: str = None,
    runtime_budget_seconds: int = None,
    release_id: str = None,
    deploy_smoke_commands: list = None,
    auto_rollback_deploy: bool = True,
) -> dict[str, Any]:
    """Fail-closed bugfix pipeline with transactional rollback on gate failure."""
    result = SafetyOrchestrationService(ctx).safe_fix(
        feature_spec=feature_spec,
        target_file=target_file,
        search=search,
        replace=replace,
        edits=edits,
        symbol=symbol,
        new_code=new_code,
        start_line=start_line,
        end_line=end_line,
        occurrence=occurrence,
        file_edits=file_edits,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        risk_domains=risk_domains,
        expected_schema_fields=expected_schema_fields,
        schema_entity=schema_entity,
        override_token=override_token,
        break_glass_token=break_glass_token,
        estimated_escalation_cost=estimated_escalation_cost,
        confidence_score=confidence_score,
        patch_primitive=patch_primitive,
        execution_profile=execution_profile,
        runtime_budget_seconds=runtime_budget_seconds,
        release_id=release_id,
        deploy_smoke_commands=deploy_smoke_commands,
        auto_rollback_deploy=auto_rollback_deploy,
    )
    return _compact_response("safe_fix", result, ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def replay_session(run_id: str, ctx: Context) -> dict[str, Any]:
    """Replay and verify hash-chained audit trail for a safety run."""
    return _compact_response("replay_session", SafetyOrchestrationService(ctx).replay_session(run_id), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def conformance_matrix(ctx: Context) -> dict[str, Any]:
    """Conformance Matrix report for framework adapters (pass/fail by required methods)."""
    return SafetyOrchestrationService(ctx).conformance_matrix()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def gate_evidence_pack(ctx: Context, run_id: str = None, limit: int = 25) -> dict[str, Any]:
    """Gate Evidence Pack report for write/merge/deploy decisions and rollback evidence."""
    return _compact_response(
        "gate_evidence_pack",
        SafetyOrchestrationService(ctx).gate_evidence_pack(run_id=run_id, limit=limit),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def kpi_report(ctx: Context, window_days: int = 30) -> dict[str, Any]:
    """KPI Report with thresholds: >=95, >=90, <=2, and 0 critical regressions."""
    return SafetyOrchestrationService(ctx).kpi_report(window_days=window_days)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def open_risk_register(ctx: Context, closure_days: int = 14, limit: int = 200) -> dict[str, Any]:
    """Open-Risk Register report: unresolved assumptions + blocked/failed runs."""
    return _compact_response(
        "open_risk_register",
        SafetyOrchestrationService(ctx).open_risk_register(closure_days=closure_days, limit=limit),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_scope_inventory(ctx: Context) -> dict[str, Any]:
    """List framework adapter scope inventory with owner/due date/done criteria."""
    return SafetyOrchestrationService(ctx).get_scope_inventory()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def upsert_scope_owner(
    framework: str,
    owner: str,
    due_date: str,
    done_criteria: str,
    ctx: Context,
    status: str = "planned",
) -> dict[str, Any]:
    """Set adapter owner + due date + done criteria + status for scope governance."""
    return SafetyOrchestrationService(ctx).upsert_scope_owner(
        framework=framework,
        owner=owner,
        due_date=due_date,
        done_criteria=done_criteria,
        status=status,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_kpi_protocol(ctx: Context) -> dict[str, Any]:
    """Get KPI measurement protocol: sample size, baseline window, drift threshold, error budget."""
    return SafetyOrchestrationService(ctx).get_kpi_protocol()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def set_kpi_protocol(
    sample_size_min: int,
    baseline_window_days: int,
    measurement_method: str,
    error_budget_percent: float,
    drift_threshold_percent: float,
    thresholds: dict,
    ctx: Context,
) -> dict[str, Any]:
    """Update KPI measurement protocol used by release gates."""
    return SafetyOrchestrationService(ctx).set_kpi_protocol(
        sample_size_min=sample_size_min,
        baseline_window_days=baseline_window_days,
        measurement_method=measurement_method,
        error_budget_percent=error_budget_percent,
        drift_threshold_percent=drift_threshold_percent,
        thresholds=thresholds,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def request_policy_change(
    requested_by: str,
    reason: str,
    policy: dict,
    ctx: Context,
    required_approvals: int = 2,
) -> dict[str, Any]:
    """Create policy change request; requires approvals before activation."""
    return SafetyOrchestrationService(ctx).request_policy_change(
        requested_by=requested_by,
        reason=reason,
        policy=policy,
        required_approvals=required_approvals,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def approve_policy_change(
    change_id: str,
    approver: str,
    ctx: Context,
    note: str = None,
) -> dict[str, Any]:
    """Approve pending policy change (multi-approval flow)."""
    return SafetyOrchestrationService(ctx).approve_policy_change(change_id=change_id, approver=approver, note=note)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_policy_changes(ctx: Context, status: str = None, limit: int = 100) -> dict[str, Any]:
    """List policy change requests and active approved policy."""
    return _compact_response(
        "list_policy_changes",
        SafetyOrchestrationService(ctx).list_policy_changes(status=status, limit=limit),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def rotate_signing_key(
    key_name: str,
    old_value: str,
    new_value: str,
    rotated_by: str,
    ctx: Context,
    note: str = "",
) -> dict[str, Any]:
    """Record signing key rotation with fingerprints for audit trail."""
    return SafetyOrchestrationService(ctx).rotate_signing_key(
        key_name=key_name,
        old_value=old_value,
        new_value=new_value,
        rotated_by=rotated_by,
        note=note,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_key_rotations(ctx: Context, key_name: str = None, limit: int = 100) -> dict[str, Any]:
    """List key rotation history."""
    return SafetyOrchestrationService(ctx).list_key_rotations(key_name=key_name, limit=limit)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_benchmark_harness(
    ctx: Context,
    sample_size: int = 2000,
    seed: int = 42,
    stratified: bool = True,
) -> dict[str, Any]:
    """Run benchmark harness with stratified synthetic workload + KPI checks."""
    return _compact_response(
        "run_benchmark_harness",
        SafetyOrchestrationService(ctx).run_benchmark_harness(
            sample_size=sample_size,
            seed=seed,
            stratified=stratified,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_benchmark_runs(ctx: Context, limit: int = 50) -> dict[str, Any]:
    """List historical benchmark harness runs."""
    return _compact_response(
        "list_benchmark_runs",
        SafetyOrchestrationService(ctx).list_benchmark_runs(limit=limit),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def request_break_glass(
    requested_by: str,
    reason: str,
    ctx: Context,
    scope: str = "global",
    ttl_minutes: Optional[int] = None,
    required_approvals: Optional[int] = None,
) -> dict[str, Any]:
    """Create break-glass request for critical path override."""
    return SafetyOrchestrationService(ctx).request_break_glass(
        requested_by=requested_by,
        reason=reason,
        scope=scope,
        ttl_minutes=ttl_minutes,
        required_approvals=required_approvals,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def approve_break_glass(
    request_id: str,
    approver: str,
    ctx: Context,
    note: str = None,
) -> dict[str, Any]:
    """Approve break-glass request; returns token once quorum is reached."""
    return SafetyOrchestrationService(ctx).approve_break_glass(request_id=request_id, approver=approver, note=note)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_break_glass_request(request_id: str, ctx: Context) -> dict[str, Any]:
    """Fetch break-glass request details and approvals."""
    return SafetyOrchestrationService(ctx).get_break_glass_request(request_id=request_id)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def create_audit_backup(ctx: Context, created_by: str = "system") -> dict[str, Any]:
    """Create audit/governance backup archive with checksum."""
    return SafetyOrchestrationService(ctx).create_audit_backup(created_by=created_by)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def list_audit_backups(ctx: Context, limit: int = 50) -> dict[str, Any]:
    """List backup archives."""
    return SafetyOrchestrationService(ctx).list_audit_backups(limit=limit)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def restore_audit_backup(backup_id: str, ctx: Context, dry_run: bool = True) -> dict[str, Any]:
    """Restore backup archive (or verify only with dry_run=true)."""
    return SafetyOrchestrationService(ctx).restore_audit_backup(backup_id=backup_id, dry_run=dry_run)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_dr_drill(ctx: Context, created_by: str = "drill") -> dict[str, Any]:
    """Run backup + restore verification drill."""
    return SafetyOrchestrationService(ctx).run_dr_drill(created_by=created_by)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def create_rollout_plan(release_id: str, ctx: Context, stages: list = None) -> dict[str, Any]:
    """Create staged rollout plan (canary -> full)."""
    return SafetyOrchestrationService(ctx).create_rollout_plan(release_id=release_id, stages=stages)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def execute_rollout_stage(
    release_id: str,
    stage: str,
    traffic_percent: float,
    ctx: Context,
    smoke_commands: list = None,
    auto_rollback: bool = True,
) -> dict[str, Any]:
    """Execute one rollout stage and rollback automatically on failed smoke commands."""
    return SafetyOrchestrationService(ctx).execute_rollout_stage(
        release_id=release_id,
        stage=stage,
        traffic_percent=traffic_percent,
        smoke_commands=smoke_commands,
        auto_rollback=auto_rollback,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_rollout_status(release_id: str, ctx: Context) -> dict[str, Any]:
    """Get rollout status/events for a release."""
    return _compact_response("get_rollout_status", SafetyOrchestrationService(ctx).get_rollout_status(release_id), ctx)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def run_security_quality_suite(ctx: Context, include_redteam: bool = True) -> dict[str, Any]:
    """Run prompt-injection red-team, PII redaction, escalation-cap and rollout safety checks."""
    return _compact_response(
        "run_security_quality_suite",
        SafetyOrchestrationService(ctx).run_security_quality_suite(include_redteam=include_redteam),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def release_readiness_report(
    ctx: Context,
    window_days: int = 30,
    closure_days: int = 14,
    include_security_suite: bool = True,
) -> dict[str, Any]:
    """Aggregate production release readiness gate with required reports + security suite."""
    return _compact_response(
        "release_readiness_report",
        SafetyOrchestrationService(ctx).release_readiness_report(
            window_days=window_days,
            closure_days=closure_days,
            include_security_suite=include_security_suite,
        ),
        ctx,
    )


# ----- PROMPTS -----
# Removed: analyze_code, code_search, set_project prompts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the MCP server."""
    parser = argparse.ArgumentParser(description="Blindspot MCP server")
    parser.add_argument(
        "--project-path",
        dest="project_path",
        help="Set the project path on startup (equivalent to calling set_project_path).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol to use (default: stdio).",
    )
    parser.add_argument(
        "--mount-path",
        dest="mount_path",
        default=None,
        help="Mount path when using SSE transport.",
    )
    parser.add_argument(
        "--indexer-path",
        dest="indexer_path",
        default=None,
        help="Custom path for storing indices (overrides default /tmp/blindspot_index location).",
    )
    parser.add_argument(
        "--tool-prefix",
        dest="tool_prefix",
        default=None,
        help="Prefix to add to all tool names (e.g., 'prefix:' -> 'prefix:tool_name').",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)."
    )
    parser.add_argument(
        "--framework",
        dest="framework",
        default=None,
        help="Framework to load (e.g., 'laravel', 'nextjs', 'django'). Auto-detected if omitted.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    """Main function to run the MCP server."""
    # Setup signal handlers only when running as main process (not on import)
    _setup_signal_handlers()

    args = _parse_args(argv)

    # Load and register framework plugins
    from .plugins import load_builtin_plugins, register_plugin_tools
    from .adapters.project_structure import get_project_structure, detect_workspaces
    load_builtin_plugins()

    # Determine framework(s): CLI flag > monorepo detection > single detection > none
    frameworks_to_load: set = set()

    if args.framework:
        # Manual override — single framework
        frameworks_to_load.add(args.framework)
    elif args.project_path:
        try:
            # Check for monorepo (multiple workspaces with different frameworks)
            workspaces = detect_workspaces(args.project_path)
            if workspaces:
                for ws in workspaces:
                    if ws.get("framework"):
                        frameworks_to_load.add(ws["framework"])
                        logger.info("Detected workspace '%s': %s (%s)",
                                    ws["name"], ws["framework"], ws["language"])

            # Also check root-level framework
            structure = get_project_structure(args.project_path)
            if structure.framework and structure.framework != "none":
                frameworks_to_load.add(structure.framework)
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)

    # Register plugin tools for all detected frameworks
    # In a monorepo like frontend(Next.js) + backend(NestJS), both plugins load
    if frameworks_to_load:
        for fw in frameworks_to_load:
            logger.info("Loading plugin: %s", fw)
            register_plugin_tools(mcp, framework=fw)
    else:
        logger.info("No framework detected — core tools only")

    # Store CLI configuration for lifespan bootstrap.
    _CLI_CONFIG.project_path = args.project_path

    # Configure custom index root if provided
    if args.indexer_path:
        # Patch ProjectSettings class to use the custom root
        ProjectSettings.custom_index_root = args.indexer_path

        # Ensure the directory exists
        try:
            os.makedirs(args.indexer_path, exist_ok=True)
        except Exception as e:
            logger.error(
                f"Failed to create custom indexer path {args.indexer_path}: {e}"
            )
            sys.exit(1)

    # Rename tools if prefix is provided
    if args.tool_prefix:
        prefix = args.tool_prefix
        try:
            # Access internal tool registry (FastMCP specific)
            # FastMCP stores tools in _tool_manager._tools or directly in _tools
            # We need to support both for resilience
            tool_registry = None
            if hasattr(mcp, "_tool_manager") and hasattr(mcp._tool_manager, "_tools"):
                tool_registry = mcp._tool_manager._tools
            elif hasattr(mcp, "_tools"):
                tool_registry = mcp._tools

            if tool_registry:
                # Create a new registry with prefixed names
                new_registry = {}
                for name, tool in tool_registry.items():
                    new_name = f"{prefix}{name}"
                    tool.name = new_name
                    new_registry[new_name] = tool

                # Replace the registry
                if hasattr(mcp, "_tool_manager") and hasattr(
                    mcp._tool_manager, "_tools"
                ):
                    mcp._tool_manager._tools = new_registry
                elif hasattr(mcp, "_tools"):
                    mcp._tools = new_registry

                logger.info(
                    f"Applied tool prefix '{prefix}' to {len(new_registry)} tools"
                )
            else:
                logger.warning("Could not find tool registry to apply prefix")

        except Exception as e:
            logger.error(f"Failed to apply tool prefix: {e}")
            # Fatal error: cannot apply requested prefix
            sys.exit(1)

    # For HTTP transports, add project context middleware for per-project isolation
    if args.transport in ("sse", "streamable-http"):
        import asyncio
        import uvicorn
        from .middleware import ProjectContextMiddleware

        # Set port via settings
        mcp.settings.port = args.port

        # Get the appropriate Starlette app
        if args.transport == "sse":
            starlette_app = mcp.sse_app(args.mount_path)
        else:
            starlette_app = mcp.streamable_http_app()

        # Add project context middleware for per-project manager isolation
        starlette_app.add_middleware(ProjectContextMiddleware)
        logger.info("Added ProjectContextMiddleware for per-project isolation")

        # Run with uvicorn
        config = uvicorn.Config(
            starlette_app,
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        try:
            asyncio.run(server.serve())
        except RuntimeError as exc:
            logger.error("MCP server terminated with error: %s", exc)
            raise SystemExit(1) from exc
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Unexpected MCP server error: %s", exc)
            raise
    else:
        # For stdio transport, use default run method
        try:
            mcp.run(transport=args.transport)
        except RuntimeError as exc:
            logger.error("MCP server terminated with error: %s", exc)
            raise SystemExit(1) from exc
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Unexpected MCP server error: %s", exc)
            raise


if __name__ == "__main__":
    main()
