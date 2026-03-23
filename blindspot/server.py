"""
Blindspot MCP Server

This MCP server allows LLMs to index, search, and analyze code from a project directory.
It provides tools for file discovery, content retrieval, and code analysis.

This version uses a service-oriented architecture where MCP decorators delegate
to domain-specific services for business logic.
"""

# Standard library imports
import argparse
import inspect
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
from .services import FileService, FileWatcherService, SearchService, SettingsService
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
                    # Timeout: skip our ticket so others can proceed
                    if self._serving_ticket == my_ticket:
                        self._serving_ticket += 1
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

_setup_signal_handlers()


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


# Setup logging without writing to files
def setup_indexing_performance_logging():
    """Setup logging (stderr only); remove any file-based logging."""

    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # stderr for errors only
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(logging.ERROR)

    root_logger.addHandler(stderr_handler)
    root_logger.setLevel(logging.DEBUG)


# Initialize logging (no file handlers)
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


# Create the MCP server with lifespan manager
mcp = FastMCP("Blindspot", lifespan=indexer_lifespan, dependencies=["pathlib"])

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
    return SearchService(ctx).search_code(
        pattern=pattern,
        case_sensitive=case_sensitive,
        context_lines=context_lines,
        file_pattern=file_pattern,
        fuzzy=fuzzy,
        regex=regex,
        start_index=start_index,
        max_results=max_results,
    )


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
    return CodeIntelligenceService(ctx).analyze_file(file_path)


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
    return CodeIntelligenceService(ctx).get_symbol_body(file_path, symbol_name, compact=compact)


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
    return GenericIntelligenceService(ctx).find_references(symbol, scope, context_filter=model_context)


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
    return GenericIntelligenceService(ctx).get_class_hierarchy(class_name)


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
    return GenericIntelligenceService(ctx).get_impact_analysis(file_path)


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
    return GenericIntelligenceService(ctx).get_ripple_effect(file_path, symbol, change_type)


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
    return GenericIntelligenceService(ctx).get_project_snapshot()


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
    return FileEditService(ctx).apply_edit(
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
    return AdvancedAnalysisService(ctx).analyze_queries(controller, method)


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
    return AdvancedAnalysisService(ctx).rename_symbol(file_path, old_name, new_name, dry_run)


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
    return AdvancedAnalysisService(ctx).check_eager_loading(file_path)


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
    return AdvancedAnalysisService(ctx).detect_cache_conflicts(cache_key)


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
    return AdvancedAnalysisService(ctx).diff_preview(edits)


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
    return GenericIntelligenceService(ctx).get_context_for_edit(file_path, symbol)


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

    Args: Same as apply_edit — all 5 modes (search-replace, batch, symbol, line-range, occurrence).
    """
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
        except Exception:
            pass

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
