"""Base plugin interface for Blindspot MCP framework plugins.

All framework plugins (Laravel, Django, Rails, etc.) must inherit from
BlindspotPlugin and implement the required methods.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP


class BlindspotPlugin(ABC):
    """Abstract base class for Blindspot framework plugins.

    Plugins extend Blindspot with framework-specific intelligence tools.
    Each plugin registers its own MCP tools and provides framework-specific
    analysis capabilities.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g., 'laravel', 'django', 'rails')."""
        ...

    @property
    @abstractmethod
    def framework(self) -> str:
        """Framework this plugin supports (matched against .blindspot.yaml 'framework')."""
        ...

    @property
    def description(self) -> str:
        """Human-readable plugin description."""
        return f"{self.framework} plugin for Blindspot MCP"

    @abstractmethod
    def register_tools(self, mcp: FastMCP) -> None:
        """Register this plugin's MCP tools on the server.

        Args:
            mcp: The FastMCP server instance to register tools on
        """
        ...

    def get_scan_dirs(self) -> Dict[str, str]:
        """Return default scan directory mappings for this framework.

        Returns:
            Dict mapping category names to relative directory paths.
            e.g., {"models": "app/Models", "controllers": "app/Http/Controllers"}
        """
        return {}

    def get_default_anti_patterns(self) -> List[Dict[str, Any]]:
        """Return default anti-pattern rules for this framework.

        Returns:
            List of anti-pattern rule dicts with keys:
            pattern, severity, message, file_types
        """
        return []
