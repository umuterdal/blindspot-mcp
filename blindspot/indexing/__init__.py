"""Code indexing utilities for the MCP server.

This module keeps heavyweight parser/index imports lazy so the core context
profile can start without immediately importing every tree-sitter backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .qualified_names import generate_qualified_name, normalize_file_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .deep_index_manager import DeepIndexManager
    from .json_index_builder import IndexMetadata, JSONIndexBuilder
    from .models import FileInfo, SymbolInfo
    from .shallow_index_manager import ShallowIndexManager
    from .sqlite_index_builder import SQLiteIndexBuilder
    from .sqlite_index_manager import SQLiteIndexManager


def get_index_manager():
    """Return the SQLite index manager for the current request context."""
    from ..project_manager_cache import get_index_manager_for_request

    return get_index_manager_for_request()


def get_shallow_index_manager():
    """Return the shallow index manager for the current request context."""
    from ..project_manager_cache import get_shallow_index_manager_for_request

    return get_shallow_index_manager_for_request()


def get_manager_cache():
    """Get the global manager cache singleton."""
    from ..project_manager_cache import get_manager_cache as _get_cache

    return _get_cache()


def __getattr__(name: str) -> Any:
    """Load heavyweight indexing classes on demand."""
    if name == "JSONIndexBuilder" or name == "IndexMetadata":
        from .json_index_builder import JSONIndexBuilder, IndexMetadata

        return {"JSONIndexBuilder": JSONIndexBuilder, "IndexMetadata": IndexMetadata}[name]
    if name == "SQLiteIndexBuilder":
        from .sqlite_index_builder import SQLiteIndexBuilder

        return SQLiteIndexBuilder
    if name == "SQLiteIndexManager":
        from .sqlite_index_manager import SQLiteIndexManager

        return SQLiteIndexManager
    if name == "ShallowIndexManager":
        from .shallow_index_manager import ShallowIndexManager

        return ShallowIndexManager
    if name == "DeepIndexManager":
        from .deep_index_manager import DeepIndexManager

        return DeepIndexManager
    if name == "SymbolInfo" or name == "FileInfo":
        from .models import FileInfo, SymbolInfo

        return {"SymbolInfo": SymbolInfo, "FileInfo": FileInfo}[name]
    raise AttributeError(name)


__all__ = [
    "generate_qualified_name",
    "normalize_file_path",
    "get_index_manager",
    "get_shallow_index_manager",
    "get_manager_cache",
    "JSONIndexBuilder",
    "IndexMetadata",
    "SQLiteIndexBuilder",
    "SQLiteIndexManager",
    "ShallowIndexManager",
    "DeepIndexManager",
    "SymbolInfo",
    "FileInfo",
]
