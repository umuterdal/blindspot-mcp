"""Blindspot MCP plugin system.

Provides a registry for framework-specific plugins that extend
Blindspot with additional MCP tools and analysis capabilities.
"""

import logging
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .base_plugin import BlindspotPlugin

logger = logging.getLogger(__name__)

# Global plugin registry
_plugins: Dict[str, BlindspotPlugin] = {}


def register_plugin(plugin: BlindspotPlugin) -> None:
    """Register a plugin in the global registry.

    Args:
        plugin: Plugin instance to register
    """
    _plugins[plugin.name] = plugin
    logger.info("Registered plugin: %s (%s)", plugin.name, plugin.description)


def get_plugin(name: str) -> Optional[BlindspotPlugin]:
    """Get a registered plugin by name.

    Args:
        name: Plugin name

    Returns:
        Plugin instance, or None if not registered
    """
    return _plugins.get(name)


def get_all_plugins() -> List[BlindspotPlugin]:
    """Get all registered plugins."""
    return list(_plugins.values())


def load_builtin_plugins() -> None:
    """Load all built-in plugins (e.g., Laravel, Next.js)."""
    try:
        from .laravel import LaravelPlugin
        register_plugin(LaravelPlugin())
    except ImportError as e:
        logger.debug("Laravel plugin not available: %s", e)

    try:
        from .nextjs import NextjsPlugin
        register_plugin(NextjsPlugin())
    except ImportError as e:
        logger.debug("Next.js plugin not available: %s", e)

    try:
        from .nuxt import NuxtPlugin
        register_plugin(NuxtPlugin())
    except ImportError as e:
        logger.debug("Nuxt plugin not available: %s", e)

    try:
        from .sveltekit import SvelteKitPlugin
        register_plugin(SvelteKitPlugin())
    except ImportError as e:
        logger.debug("SvelteKit plugin not available: %s", e)

    try:
        from .django import DjangoPlugin
        register_plugin(DjangoPlugin())
    except ImportError as e:
        logger.debug("Django plugin not available: %s", e)

    try:
        from .spring import SpringPlugin
        register_plugin(SpringPlugin())
    except ImportError as e:
        logger.debug("Spring Boot plugin not available: %s", e)

    try:
        from .express import ExpressPlugin
        register_plugin(ExpressPlugin())
    except ImportError as e:
        logger.debug("Express plugin not available: %s", e)

    try:
        from .go import GoPlugin
        register_plugin(GoPlugin())
    except ImportError as e:
        logger.debug("Go plugin not available: %s", e)

    try:
        from .rails import RailsPlugin
        register_plugin(RailsPlugin())
    except ImportError as e:
        logger.debug("Rails plugin not available: %s", e)

    try:
        from .fastapi import FastAPIPlugin
        register_plugin(FastAPIPlugin())
    except ImportError as e:
        logger.debug("FastAPI plugin not available: %s", e)

    try:
        from .flutter import FlutterPlugin
        register_plugin(FlutterPlugin())
    except ImportError as e:
        logger.debug("Flutter plugin not available: %s", e)

    try:
        from .aspnet import AspNetPlugin
        register_plugin(AspNetPlugin())
    except ImportError as e:
        logger.debug("ASP.NET Core plugin not available: %s", e)

    try:
        from .reactnative import ReactNativePlugin
        register_plugin(ReactNativePlugin())
    except ImportError as e:
        logger.debug("React Native plugin not available: %s", e)

    try:
        from .nestjs import NestJSPlugin
        register_plugin(NestJSPlugin())
    except ImportError as e:
        logger.debug("NestJS plugin not available: %s", e)

    try:
        from .rust import RustPlugin
        register_plugin(RustPlugin())
    except ImportError as e:
        logger.debug("Rust plugin not available: %s", e)

    try:
        from .phoenix import PhoenixPlugin
        register_plugin(PhoenixPlugin())
    except ImportError as e:
        logger.debug("Phoenix plugin not available: %s", e)


def register_plugin_tools(mcp: FastMCP, framework: Optional[str] = None) -> None:
    """Register tools from the plugin matching the specified framework.

    If framework is None, no plugin tools are registered (only core tools remain).
    If framework is specified, only that plugin's tools are registered.

    Args:
        mcp: FastMCP server instance
        framework: Framework name to load, or None for core-only
    """
    if framework is None:
        logger.info("No framework specified — loading core tools only (no plugin tools)")
        return

    for plugin in _plugins.values():
        if plugin.framework == framework:
            try:
                plugin.register_tools(mcp)
                logger.info("Loaded tools from plugin: %s", plugin.name)
            except Exception as e:
                logger.error("Failed to load plugin %s: %s", plugin.name, e)


__all__ = [
    "BlindspotPlugin",
    "register_plugin",
    "get_plugin",
    "get_all_plugins",
    "load_builtin_plugins",
    "register_plugin_tools",
]
