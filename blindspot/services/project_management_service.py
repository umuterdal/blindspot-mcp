"""Project initialization for the core context engine."""

from __future__ import annotations

import logging
import os
from typing import List

from .base_service import BaseService
from ..indexing import get_index_manager, get_shallow_index_manager
from ..project_settings import ProjectSettings

logger = logging.getLogger(__name__)


class ProjectManagementService(BaseService):
    """Initialize project path, settings, and indices for the active session."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._index_manager = get_index_manager()
        self._shallow_manager = get_shallow_index_manager()

    def initialize_project(self, path: str) -> str:
        normalized_path = self._validate_and_normalize_path(path)
        settings = ProjectSettings(normalized_path, skip_load=False)

        self.helper.update_base_path(normalized_path)
        self.helper.update_settings(settings)

        excludes = self._get_exclude_patterns(settings)
        if not self._shallow_manager.set_project_path(normalized_path, excludes):
            raise RuntimeError(f"Failed to set shallow project path: {normalized_path}")
        if not self._shallow_manager.load_index():
            if not self._shallow_manager.build_index():
                raise RuntimeError("Failed to build shallow index")

        file_count = len(self._shallow_manager.get_file_list())
        self.helper.update_file_count(file_count)

        if self._index_manager.set_project_path(normalized_path, excludes):
            try:
                self._index_manager.load_index()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Deep index auto-load failed: %s", exc)

        return f"Project initialized: {normalized_path} ({file_count} files)"

    def _validate_and_normalize_path(self, path: str) -> str:
        if not path or not path.strip():
            raise ValueError("Project path cannot be empty")

        normalized = os.path.abspath(os.path.normpath(path))
        if not os.path.exists(normalized):
            raise ValueError(f"Path does not exist: {normalized}")
        if not os.path.isdir(normalized):
            raise ValueError(f"Path is not a directory: {normalized}")
        return normalized

    @staticmethod
    def _get_exclude_patterns(settings: ProjectSettings) -> List[str]:
        patterns: List[str] = []
        try:
            config = settings.get_file_watcher_config()
        except Exception:
            config = {}
        for key in ("exclude_patterns", "additional_exclude_patterns"):
            for pattern in config.get(key) or []:
                if isinstance(pattern, str) and pattern.strip():
                    patterns.append(pattern.strip())
        return patterns
