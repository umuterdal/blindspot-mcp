"""Generic Intelligence Service — language-agnostic code intelligence.

This service replaces the hardcoded Laravel paths in LaravelIntelligenceService
for core tools (find_references, get_class_hierarchy, get_impact_analysis,
get_ripple_effect, get_project_snapshot, get_context_for_edit).

It delegates to SymbolResolver which uses:
- Deep index (SQLite + tree-sitter) for symbol data
- ProjectStructure adapter for directory resolution
- LanguageSyntax adapter for language-specific patterns

Works with PHP, JavaScript/TypeScript, Python, Go, Rust, Java, Ruby, and more.
"""

import logging
import os
from typing import Any, Dict, Optional

from .base_service import BaseService
from ..adapters.project_structure import get_project_structure, ProjectStructure
from ..adapters.symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)


class GenericIntelligenceService(BaseService):
    """Language-agnostic code intelligence for any project."""

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    def _get_resolver(self) -> Optional[SymbolResolver]:
        """Create a SymbolResolver for the current project."""
        base = self._get_project_path()
        if not base:
            return None

        structure = get_project_structure(base)
        index_mgr = self.index_manager
        return SymbolResolver(base, structure, index_mgr)

    def _get_structure(self) -> Optional[ProjectStructure]:
        """Get project structure for the current project."""
        base = self._get_project_path()
        if not base:
            return None
        return get_project_structure(base)

    # ── find_references ──────────────────────────────────────────

    def find_references(self, symbol: str, scope: str = "all",
                        context_filter: Optional[str] = None) -> Dict[str, Any]:
        """Find all files that reference a symbol.

        Language-agnostic: scans all source files using config scan_dirs
        and language syntax adapter for usage classification.

        Args:
            symbol: Symbol name to search for
            scope: Category filter ('all', 'models', 'controllers', etc.)
            context_filter: Optional class name for context filtering
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        result = resolver.find_references(symbol, scope, context_filter)
        result["status"] = "success"
        return result

    # ── get_class_hierarchy ──────────────────────────────────────

    def get_class_hierarchy(self, class_name: str) -> Dict[str, Any]:
        """Get full inheritance/implementation chain for any class.

        Works with PHP, Python, TypeScript, Java, Go (struct embedding),
        Rust (trait impl), Ruby, and more.

        Args:
            class_name: Class name to look up
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        return resolver.get_class_hierarchy(class_name)

    # ── get_impact_analysis ──────────────────────────────────────

    def get_impact_analysis(self, file_path: str) -> Dict[str, Any]:
        """Analyze what would be affected if a file is modified.

        Uses deep index for symbol extraction, then cross-references
        across the entire codebase.

        Args:
            file_path: Relative path to analyze
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        return resolver.get_impact_analysis(file_path)

    # ── get_ripple_effect ────────────────────────────────────────

    def get_ripple_effect(self, file_path: str, symbol: str,
                          change_type: str = "modify") -> Dict[str, Any]:
        """Trace the full ripple effect of changing a specific symbol.

        Language-agnostic: uses config scan_dirs for categorization,
        syntax adapter for usage classification.

        Args:
            file_path: File containing the symbol
            symbol: Symbol name
            change_type: 'modify', 'rename', or 'delete'
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        return resolver.get_ripple_effect(file_path, symbol, change_type)

    # ── get_project_snapshot ─────────────────────────────────────

    def get_project_snapshot(self) -> Dict[str, Any]:
        """Generate a compact snapshot of the entire project.

        Language-agnostic: uses deep index for symbol data,
        config scan_dirs for categorization.
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        return resolver.get_project_snapshot()

    # ── get_context_for_edit ─────────────────────────────────────

    def get_context_for_edit(self, file_path: str,
                              symbol: Optional[str] = None) -> Dict[str, Any]:
        """Auto-gather ALL context needed before editing a file.

        The "external brain" — call this ONCE before editing.
        Works with any language/framework.

        Args:
            file_path: File about to be edited
            symbol: Optional symbol to focus on
        """
        resolver = self._get_resolver()
        if not resolver:
            return {"status": "error", "message": "Project path not set"}

        return resolver.get_context_for_edit(file_path, symbol)
