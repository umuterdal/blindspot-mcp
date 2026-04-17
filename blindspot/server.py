"""Blindspot MCP server.

This server intentionally exposes only the core context-engine surface.
"""

from __future__ import annotations

import argparse
import json
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
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP

from .project_settings import ProjectSettings
from .services.code_intelligence_service import CodeIntelligenceService
from .services.context_engine_service import ContextEngineService
from .services.file_discovery_service import FileDiscoveryService
from .services.file_edit_service import FileEditService
from .services.index_management_service import IndexManagementService
from .services.project_management_service import ProjectManagementService
from .services.search_service import SearchService
from .utils import handle_mcp_tool_errors

MAX_CONCURRENT_REQUESTS = 5
MAX_DETAIL_FILES = 20


class FIFOConcurrencyLimiter:
    """FIFO queue-based concurrency limiter."""

    def __init__(self, max_concurrent: int, timeout: float = 60.0):
        self._max_concurrent = max_concurrent
        self._timeout = timeout
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._active_count = 0
        self._next_ticket = 0
        self._serving_ticket = 0

    def acquire(self, timeout: float | None = None) -> int:
        timeout = timeout or self._timeout
        with self._condition:
            my_ticket = self._next_ticket
            self._next_ticket += 1
            start = time.monotonic()

            while self._serving_ticket != my_ticket or self._active_count >= self._max_concurrent:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    if self._serving_ticket <= my_ticket:
                        self._serving_ticket = my_ticket + 1
                    self._condition.notify_all()
                    raise TimeoutError(f"Queue timeout after {timeout}s (ticket {my_ticket})")
                self._condition.wait(timeout=min(remaining, 1.0))

            self._active_count += 1
            self._serving_ticket += 1
            self._condition.notify_all()
            return my_ticket

    def release(self) -> None:
        with self._condition:
            self._active_count -= 1
            self._condition.notify_all()


_concurrency_limiter = FIFOConcurrencyLimiter(MAX_CONCURRENT_REQUESTS)


def _setup_signal_handlers() -> None:
    """Setup signal handlers for stable MCP startup/shutdown."""

    def sigint_handler(_signum, _frame):
        logging.getLogger(__name__).warning("Received SIGINT - ignoring for session stability")

    def sigterm_handler(_signum, _frame):
        logging.getLogger(__name__).info("Received SIGTERM - shutting down gracefully")
        sys.exit(0)

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, sigint_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sigterm_handler)


def with_concurrency_limit(func):
    """Decorator to limit concurrent tool execution."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            _concurrency_limiter.acquire()
        except TimeoutError as exc:
            logging.getLogger(__name__).warning("Queue timeout for %s: %s", func.__name__, exc)
            return {
                "status": "error",
                "error": "queue_timeout",
                "message": f"Server busy, request queued too long. Please retry. ({exc})",
            }
        try:
            return func(*args, **kwargs)
        finally:
            _concurrency_limiter.release()

    return wrapper


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("blindspot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handler.setLevel(logging.ERROR)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    return logging.getLogger(__name__)


logger = _setup_logging()


@dataclass
class BlindspotContext:
    """Context for the Blindspot MCP server."""

    base_path: str
    settings: ProjectSettings
    file_count: int = 0


@dataclass
class _CLIConfig:
    project_path: str | None = None


class _BootstrapRequestContext:
    """Minimal request context to bootstrap project services."""

    def __init__(self, lifespan_context: BlindspotContext):
        self.lifespan_context = lifespan_context
        self.session = None
        self.meta = None


_CLI_CONFIG = _CLIConfig()


@asynccontextmanager
async def indexer_lifespan(_server: FastMCP) -> AsyncIterator[BlindspotContext]:
    settings = ProjectSettings("", skip_load=True)
    context = BlindspotContext(base_path="", settings=settings, file_count=0)

    if _CLI_CONFIG.project_path:
        bootstrap_ctx = Context(
            request_context=_BootstrapRequestContext(context),
            fastmcp=mcp,
        )
        ProjectManagementService(bootstrap_ctx).initialize_project(_CLI_CONFIG.project_path)
    yield context


_INSTRUCTIONS = """
# Blindspot Context Engine

Blindspot is a local context engine for AI coding agents.
Use it to understand code before editing it.

What Blindspot is good at:
- project structure
- symbol ownership and relationships
- direct callers and indirect dependents
- likely blast radius of a change
- the smallest useful source excerpt for a safe edit

Preferred workflow:
1. `set_project_path()` once per repo.
2. `get_project_snapshot()` once per session.
3. `get_context(target=..., intent='before_edit', symbol=...)` before any important edit.
4. `get_symbol_body()` or `get_edit_region()` only when exact source is needed.
5. `search_code()` only when structured context is still insufficient.

Tool selection rules:
- Start with `get_context`. It is the main entrypoint.
- Use `get_project_snapshot` for repo-wide orientation, not for edit planning.
- Use `get_symbol_body` for one symbol, not an entire file.
- Use `get_edit_region` when you already know the symbol or lines you need.
- Use `find_files` to locate candidates, then return to `get_context`.
- Use `refresh_index` or `build_deep_index` only when index data is stale or missing.
- Pass `change_type='rename'` or `change_type='signature_change'` when planning a coordinated refactor.
"""


mcp = FastMCP("Blindspot", instructions=_INSTRUCTIONS, lifespan=indexer_lifespan, dependencies=["pathlib"])

CORE_CONTEXT_TOOL_NAMES = frozenset(
    {
        "set_project_path",
        "search_code",
        "find_files",
        "get_symbol_body",
        "refresh_index",
        "build_deep_index",
        "get_edit_region",
        "get_project_snapshot",
        "get_context",
    }
)


def _detail_output_path(base_path: str, tool_name: str) -> str:
    output_dir = os.path.join(base_path, ".blindspot", "output")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{tool_name}_{timestamp}.json"
    full_path = os.path.join(output_dir, filename)

    existing = sorted(
        [
            os.path.join(output_dir, name)
            for name in os.listdir(output_dir)
            if name.endswith(".json")
        ],
        key=os.path.getmtime,
    )
    if len(existing) >= MAX_DETAIL_FILES:
        for old_file in existing[: len(existing) - MAX_DETAIL_FILES + 1]:
            try:
                os.remove(old_file)
            except OSError:
                pass

    return full_path


def _compact_response(tool_name: str, result: Any, ctx: Context | None = None) -> Any:
    """Save oversized dict responses to a detail file."""
    if not isinstance(result, dict):
        return result

    try:
        size_estimate = len(json.dumps(result, default=str))
    except Exception:
        return result

    if size_estimate < 3500:
        return result

    base_path = ""
    if ctx is not None:
        try:
            base_path = getattr(ctx.request_context.lifespan_context, "base_path", "") or ""
        except Exception:
            base_path = ""

    if not base_path:
        return result

    try:
        full_path = _detail_output_path(base_path, tool_name)
        with open(full_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, default=str)

        compact = {
            "status": result.get("status", "success"),
            "detail_file": os.path.relpath(full_path, base_path),
            "hint": "Full response moved to detail_file to keep the context window small.",
        }
        for key in (
            "intent",
            "change_type",
            "overview",
            "project",
            "target",
            "confidence",
            "confidence_details",
            "related_files",
            "related_file_reasons",
            "risk_reasons",
            "blast_radius",
            "edit_plan",
            "message",
        ):
            if key in result:
                compact[key] = result[key]
        return compact
    except Exception:
        return result


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def set_project_path(path: str, ctx: Context) -> str:
    """Call this first. Sets the active repo and initializes the available index state."""
    return ProjectManagementService(ctx).initialize_project(path)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def search_code(
    pattern: str,
    ctx: Context,
    case_sensitive: bool = True,
    context_lines: int = 0,
    file_pattern: str | None = None,
    fuzzy: bool = False,
    regex: bool | None = None,
    start_index: int = 0,
    max_results: int | None = 10,
) -> dict[str, Any]:
    """Fallback text search. Use this only when `get_context` and symbol tools are not enough."""
    return _compact_response(
        "search_code",
        SearchService(ctx).search_code(
            pattern=pattern,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            file_pattern=file_pattern,
            fuzzy=fuzzy,
            regex=regex,
            start_index=start_index,
            max_results=max_results,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="list")
def find_files(pattern: str, ctx: Context) -> list[str]:
    """Locate likely files by glob pattern, then use `get_context` on the best candidate."""
    return FileDiscoveryService(ctx).find_files(pattern)


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_symbol_body(
    file_path: str,
    symbol_name: str,
    ctx: Context,
    compact: bool = False,
) -> dict[str, Any]:
    """Return one symbol's metadata or bounded source. Use this when exact symbol code is needed."""
    return _compact_response(
        "get_symbol_body",
        CodeIntelligenceService(ctx).get_symbol_body(file_path, symbol_name, compact=compact),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def refresh_index(ctx: Context) -> str:
    """Rebuild the shallow file index when file discovery looks stale."""
    return IndexManagementService(ctx).rebuild_index()


@mcp.tool()
@handle_mcp_tool_errors(return_type="str")
def build_deep_index(ctx: Context) -> str:
    """Build the deep symbol index required for rich relationship and impact analysis."""
    return IndexManagementService(ctx).rebuild_deep_index()


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_edit_region(
    file_path: str,
    ctx: Context,
    symbol: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    context_lines: int = 5,
) -> dict[str, Any]:
    """Return a tight numbered excerpt around a symbol or line range for precise edits."""
    return _compact_response(
        "get_edit_region",
        FileEditService(ctx).get_edit_region(
            file_path=file_path,
            symbol=symbol,
            start_line=start_line,
            end_line=end_line,
            context_lines=context_lines,
        ),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_project_snapshot(ctx: Context) -> dict[str, Any]:
    """Session-start overview of the repo. Use this once before drilling into a specific edit."""
    return _compact_response(
        "get_project_snapshot",
        ContextEngineService(ctx).get_context(intent="project"),
        ctx,
    )


@mcp.tool()
@handle_mcp_tool_errors(return_type="dict")
@with_concurrency_limit
def get_context(
    ctx: Context,
    target: str = "",
    intent: str = "before_edit",
    symbol: str | None = None,
    include_source: bool = True,
    max_related: int = 10,
    change_type: str = "modify",
    owner: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Primary tool. Returns normalized file, symbol, relationship, and impact context before editing.

    Pass ``owner`` (for example the enclosing class) when a symbol name is
    ambiguous within a file, so the engine can disambiguate identically
    named methods such as ``User.save`` vs ``Order.save``.

    Pass ``query`` as a free-text description (e.g. "pricing quote total")
    when you do not know the exact file or symbol. The engine runs a BM25
    search over the indexed symbols, resolves target/symbol/owner from
    the top match, and echoes the inference under ``query_resolution``.

    If ``target`` points to a previously-saved detail file under
    ``.blindspot/output/*.json``, the stored payload is returned verbatim
    so oversized responses never strand the caller.
    """
    detail_payload = _read_detail_file(ctx, target)
    if detail_payload is not None:
        return detail_payload

    return _compact_response(
        "get_context",
        ContextEngineService(ctx).get_context(
            target=target,
            intent=intent,
            symbol=symbol,
            include_source=include_source,
            max_related=max_related,
            change_type=change_type,
            owner=owner,
            query=query,
        ),
        ctx,
    )


def _read_detail_file(ctx: Context, target: str) -> dict[str, Any] | None:
    """Return the parsed JSON payload when ``target`` is a detail file."""
    if not target or not target.endswith(".json"):
        return None
    normalized = target.replace(os.sep, "/")
    if "/.blindspot/output/" not in f"/{normalized}":
        return None

    base_path = ""
    try:
        base_path = getattr(ctx.request_context.lifespan_context, "base_path", "") or ""
    except Exception:
        base_path = ""

    candidate = target if os.path.isabs(target) else os.path.join(base_path or ".", target)
    if not os.path.isfile(candidate):
        return None

    try:
        with open(candidate, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Detail file could not be read: {exc}",
            "detail_file": target,
        }

    if isinstance(payload, dict):
        payload.setdefault("status", "success")
        payload["served_from_detail_file"] = target
        return payload
    return {
        "status": "success",
        "detail_file": target,
        "payload": payload,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Blindspot MCP server")
    parser.add_argument(
        "--project-path",
        dest="project_path",
        help="Set the project path on startup.",
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
        help="Custom path for storing indices.",
    )
    parser.add_argument(
        "--tool-prefix",
        dest="tool_prefix",
        default=None,
        help="Prefix to add to all tool names.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000).",
    )
    return parser.parse_args(argv)


def _apply_tool_prefix(prefix: str) -> None:
    tool_registry = None
    if hasattr(mcp, "_tool_manager") and hasattr(mcp._tool_manager, "_tools"):
        tool_registry = mcp._tool_manager._tools
    elif hasattr(mcp, "_tools"):
        tool_registry = mcp._tools

    if not tool_registry:
        logger.warning("Could not find tool registry to apply prefix")
        return

    new_registry = {}
    for name, tool in tool_registry.items():
        new_name = f"{prefix}{name}"
        tool.name = new_name
        new_registry[new_name] = tool

    if hasattr(mcp, "_tool_manager") and hasattr(mcp._tool_manager, "_tools"):
        mcp._tool_manager._tools = new_registry
    elif hasattr(mcp, "_tools"):
        mcp._tools = new_registry


def main(argv: list[str] | None = None) -> None:
    _setup_signal_handlers()
    args = _parse_args(argv)
    _CLI_CONFIG.project_path = args.project_path

    if args.indexer_path:
        ProjectSettings.custom_index_root = args.indexer_path
        try:
            os.makedirs(args.indexer_path, exist_ok=True)
        except Exception as exc:
            logger.error("Failed to create custom indexer path %s: %s", args.indexer_path, exc)
            raise SystemExit(1) from exc

    if args.tool_prefix:
        try:
            _apply_tool_prefix(args.tool_prefix)
        except Exception as exc:
            logger.error("Failed to apply tool prefix: %s", exc)
            raise SystemExit(1) from exc

    if args.transport in ("sse", "streamable-http"):
        import asyncio
        import uvicorn
        from .middleware import ProjectContextMiddleware

        mcp.settings.port = args.port
        starlette_app = mcp.sse_app(args.mount_path) if args.transport == "sse" else mcp.streamable_http_app()
        starlette_app.add_middleware(ProjectContextMiddleware)

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
    else:
        try:
            mcp.run(transport=args.transport)
        except RuntimeError as exc:
            logger.error("MCP server terminated with error: %s", exc)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
