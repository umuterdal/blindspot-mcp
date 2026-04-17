"""
Abstract base class for language parsing strategies.
"""

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional
from ..models import SymbolInfo, FileInfo


class ParsingStrategy(ABC):
    """Abstract base class for language parsing strategies."""

    @abstractmethod
    def get_language_name(self) -> str:
        """Return the language name this strategy handles."""

    @abstractmethod
    def get_supported_extensions(self) -> List[str]:
        """Return list of file extensions this strategy supports."""

    @abstractmethod
    def parse_file(self, file_path: str, content: str) -> Tuple[Dict[str, SymbolInfo], FileInfo]:
        """
        Parse file content and extract symbols.

        Args:
            file_path: Path to the file being parsed
            content: File content as string

        Returns:
            Tuple of (symbols_dict, file_info)
            - symbols_dict: Maps symbol_id -> SymbolInfo
            - file_info: FileInfo with metadata about the file
        """

    def _create_symbol_id(self, file_path: str, symbol_name: str) -> str:
        """
        Create a unique symbol ID.

        Args:
            file_path: Path to the file containing the symbol
            symbol_name: Name of the symbol

        Returns:
            Unique symbol identifier in format "relative_path::symbol_name"
        """
        relative_path = self._get_relative_path(file_path)
        return f"{relative_path}::{symbol_name}"

    def _get_relative_path(self, file_path: str) -> str:
        """Normalize path for symbol identifiers relative to project root."""
        if not file_path:
            return ""

        normalized = os.path.normpath(file_path)
        if normalized == ".":
            return ""

        normalized = normalized.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]

        if not os.path.isabs(file_path):
            normalized = normalized.lstrip("/")

        return normalized or os.path.basename(file_path)

    def _extract_line_number(self, content_bytes: bytes, byte_position: int) -> int:
        """
        Extract line number from a tree-sitter byte position.

        Tree-sitter reports ``start_byte``/``end_byte`` as offsets into
        the UTF-8 buffer it parsed. Counting newlines in the decoded
        ``str`` with that offset is only correct for pure-ASCII files;
        any multi-byte character before the target shifts the slice
        boundary and reports a wrong line number. Counting the newline
        byte (``0x0A``) in the byte buffer is encoding-safe.

        Args:
            content_bytes: UTF-8 bytes of the file
            byte_position: Byte position (tree-sitter ``start_byte``)

        Returns:
            Line number (1-based)
        """
        return content_bytes[:byte_position].count(b'\n') + 1

    def _get_file_name(self, file_path: str) -> str:
        """Get just the filename from a full path."""
        return os.path.basename(file_path)

    def _safe_extract_text(self, content_bytes: bytes, start: int, end: int) -> str:
        """Byte-safe text extraction for tree-sitter spans.

        Slices the UTF-8 buffer using the parser's byte offsets and
        decodes only that window. ``errors='replace'`` protects against
        malformed inputs without crashing indexing.
        """
        try:
            return content_bytes[start:end].decode(
                'utf-8', errors='replace',
            ).strip()
        except (IndexError, TypeError):
            return ""
