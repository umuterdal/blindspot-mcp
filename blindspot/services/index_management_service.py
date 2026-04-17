"""Index lifecycle for the core context engine."""

from __future__ import annotations

from .base_service import BaseService
from ..indexing import get_index_manager, get_shallow_index_manager


class IndexManagementService(BaseService):
    """Manage shallow and deep indices for the active project."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._index_manager = get_index_manager()
        self._shallow_manager = get_shallow_index_manager()

    def rebuild_index(self) -> str:
        self._require_project_setup()
        excludes = self._get_exclude_patterns()
        if not self._shallow_manager.set_project_path(self.base_path, excludes):
            raise RuntimeError("Failed to set project path for shallow index")
        if not self._shallow_manager.build_index():
            raise RuntimeError("Failed to rebuild shallow index")

        file_count = len(self._shallow_manager.get_file_list())
        self.helper.update_file_count(file_count)
        return f"Shallow index re-built with {file_count} files."

    def rebuild_deep_index(self) -> str:
        self._require_project_setup()
        excludes = self._get_exclude_patterns()
        if not self._index_manager.set_project_path(self.base_path, excludes):
            raise RuntimeError("Failed to set project path for deep index")
        if not self._index_manager.refresh_index():
            raise RuntimeError("Failed to rebuild deep index")

        stats = self._index_manager.get_index_stats()
        return f"Deep index rebuilt. {stats.get('indexed_files', 0)} files, {stats.get('total_symbols', 0)} symbols."

    def _get_exclude_patterns(self) -> list[str]:
        patterns: list[str] = []
        if not self.settings:
            return patterns
        try:
            config = self.settings.get_file_watcher_config()
        except Exception:
            config = {}
        for key in ("exclude_patterns", "additional_exclude_patterns"):
            for pattern in config.get(key) or []:
                if isinstance(pattern, str) and pattern.strip():
                    patterns.append(pattern.strip())
        return patterns
