"""Blindspot configuration file reader.

Reads project-level configuration from .blindspot.yaml or .blindspot.json
to customize language, framework, anti-pattern rules, syntax checkers,
and scan directories.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class AntiPatternRule:
    """A single anti-pattern rule from configuration."""
    pattern: str
    severity: str = "error"
    message: str = "Anti-pattern detected"
    file_types: List[str] = field(default_factory=list)
    exclude_if_near: Optional[str] = None


@dataclass
class BlindspotConfig:
    """Parsed .blindspot.yaml/.blindspot.json configuration."""
    language: Optional[str] = None
    framework: Optional[str] = None
    anti_patterns: List[AntiPatternRule] = field(default_factory=list)
    syntax_check: Dict[str, str] = field(default_factory=dict)
    scan_dirs: Dict[str, str] = field(default_factory=dict)
    exclude_dirs: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def get_syntax_checker(self, language: str) -> Optional[str]:
        """Get the syntax check command for a language.

        Args:
            language: Language name (e.g., 'php', 'python', 'typescript')

        Returns:
            Command template with {file} placeholder, or None
        """
        return self.syntax_check.get(language)

    def get_scan_dir(self, category: str) -> Optional[str]:
        """Get the scan directory for a category.

        Args:
            category: Category name (e.g., 'models', 'controllers', 'views')

        Returns:
            Directory path relative to project root, or None
        """
        return self.scan_dirs.get(category)

    def get_anti_patterns_for_file(self, file_path: str) -> List[AntiPatternRule]:
        """Get anti-pattern rules applicable to a file.

        Args:
            file_path: File path to check against file_types

        Returns:
            List of applicable AntiPatternRule instances
        """
        ext = os.path.splitext(file_path)[1].lstrip(".")
        applicable = []
        for rule in self.anti_patterns:
            if not rule.file_types or ext in rule.file_types:
                applicable.append(rule)
        return applicable


# Default syntax checkers per language
DEFAULT_SYNTAX_CHECKERS: Dict[str, str] = {
    "php": "php -l {file}",
    "javascript": "node --check {file}",
    "typescript": "npx tsc --noEmit --pretty false {file}",
    "python": "python -m py_compile {file}",
    "go": "go vet {file}",
    "rust": "cargo check --message-format=short 2>&1",
}

# Extension to language mapping for syntax checking
EXTENSION_LANGUAGE_MAP: Dict[str, str] = {
    ".php": "php",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".py": "python",
    ".pyw": "python",
    ".go": "go",
    ".rs": "rust",
}


def _load_yaml(path: str) -> Dict[str, Any]:
    """Load a YAML file."""
    if not HAS_YAML:
        logger.warning("pyyaml not installed, skipping %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _load_json(path: str) -> Dict[str, Any]:
    """Load a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_config(project_path: str) -> BlindspotConfig:
    """Load Blindspot configuration from a project directory.

    Looks for .blindspot.yaml or .blindspot.json in the project root.
    Falls back to empty defaults if no config file is found.

    Args:
        project_path: Absolute path to the project root

    Returns:
        BlindspotConfig instance with parsed configuration
    """
    config = BlindspotConfig()

    if not project_path or not os.path.isdir(project_path):
        return config

    # Try YAML first, then JSON
    yaml_path = os.path.join(project_path, ".blindspot.yaml")
    yml_path = os.path.join(project_path, ".blindspot.yml")
    json_path = os.path.join(project_path, ".blindspot.json")

    raw: Dict[str, Any] = {}
    try:
        if os.path.isfile(yaml_path):
            raw = _load_yaml(yaml_path)
        elif os.path.isfile(yml_path):
            raw = _load_yaml(yml_path)
        elif os.path.isfile(json_path):
            raw = _load_json(json_path)
    except Exception as e:
        logger.warning("Failed to load Blindspot config: %s", e)
        return config

    if not raw:
        return config

    config.raw = raw
    config.language = raw.get("language")
    config.framework = raw.get("framework")

    # Parse anti-pattern rules
    for rule_data in raw.get("anti_patterns", []):
        if isinstance(rule_data, dict) and "pattern" in rule_data:
            config.anti_patterns.append(AntiPatternRule(
                pattern=rule_data["pattern"],
                severity=rule_data.get("severity", "error"),
                message=rule_data.get("message", "Anti-pattern detected"),
                file_types=rule_data.get("file_types", []),
                exclude_if_near=rule_data.get("exclude_if_near"),
            ))

    # Parse syntax checkers (merge with defaults)
    config.syntax_check = dict(DEFAULT_SYNTAX_CHECKERS)
    for lang, cmd in raw.get("syntax_check", {}).items():
        config.syntax_check[lang] = cmd

    # Parse scan directories
    config.scan_dirs = raw.get("scan_dirs", {})
    config.exclude_dirs = raw.get("exclude_dirs", [])

    return config


# Module-level cache: project_path -> BlindspotConfig
_config_cache: Dict[str, BlindspotConfig] = {}


def get_config(project_path: str) -> BlindspotConfig:
    """Get Blindspot configuration with caching.

    Args:
        project_path: Absolute path to the project root

    Returns:
        Cached BlindspotConfig instance
    """
    if project_path not in _config_cache:
        _config_cache[project_path] = load_config(project_path)
    return _config_cache[project_path]


def clear_config_cache(project_path: Optional[str] = None) -> None:
    """Clear the configuration cache.

    Args:
        project_path: If provided, clear only this project's cache.
                     If None, clear all cached configs.
    """
    if project_path:
        _config_cache.pop(project_path, None)
    else:
        _config_cache.clear()
