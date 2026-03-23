"""
File Watcher Tool - Pure technical component for file monitoring operations.

This tool handles low-level file watching operations without any business logic.
"""

import time
from typing import Optional, Callable
from ...utils import ContextHelper
from ...services.file_watcher_service import FileWatcherService


class FileWatcherTool:
    """
    Pure technical component for file monitoring operations.

    This tool provides low-level file watching capabilities without
    any business logic or decision making.
    """

    def __init__(self, ctx):
        self._ctx = ctx
        self._file_watcher_service: Optional[FileWatcherService] = None
        

    def create_watcher(self) -> FileWatcherService:
        """
        Create a new file watcher service instance.

        Returns:
            FileWatcherService instance
        """
        self._file_watcher_service = FileWatcherService(self._ctx)
        return self._file_watcher_service

    def start_monitoring(self, project_path: str, rebuild_callback: Callable) -> bool:
        """
        Start file monitoring for the given project path.

        Args:
            project_path: Path to monitor
            rebuild_callback: Callback function for rebuild events

        Returns:
            True if monitoring started successfully, False otherwise
        """
        if not self._file_watcher_service:
            self._file_watcher_service = self.create_watcher()

        # Validate that the project path matches the expected base path
        helper = ContextHelper(self._ctx)
        if helper.base_path and helper.base_path != project_path:
            pass

        return self._file_watcher_service.start_monitoring(rebuild_callback)

    def stop_monitoring(self) -> None:
        """Stop file monitoring if active."""
        if self._file_watcher_service:
            self._file_watcher_service.stop_monitoring()

    def is_monitoring_active(self) -> bool:
        """
        Check if file monitoring is currently active.

        Returns:
            True if monitoring is active, False otherwise
        """
        return (self._file_watcher_service is not None and
                self._file_watcher_service.is_active())

    def get_monitoring_status(self) -> dict:
        """
        Get current monitoring status.

        Returns:
            Dictionary with monitoring status information
        """
        if not self._file_watcher_service:
            return {
                'active': False,
                'available': True,
                'status': 'not_initialized'
            }

        return self._file_watcher_service.get_status()

    def store_in_context(self) -> None:
        """Store the file watcher service in the MCP context."""
        if (self._file_watcher_service and
            hasattr(self._ctx.request_context.lifespan_context, '__dict__')):
            self._ctx.request_context.lifespan_context.file_watcher_service = self._file_watcher_service

    def get_from_context(self) -> Optional[FileWatcherService]:
        """
        Get existing file watcher service from context.

        Returns:
            FileWatcherService instance or None if not found
        """
        if hasattr(self._ctx.request_context.lifespan_context, 'file_watcher_service'):
            return self._ctx.request_context.lifespan_context.file_watcher_service
        return None

    def stop_existing_watcher(self) -> None:
        """Stop any existing file watcher from context."""
        existing_watcher = self.get_from_context()
        if existing_watcher:
            
            existing_watcher.stop_monitoring()
            # Clear reference
            if hasattr(self._ctx.request_context.lifespan_context, '__dict__'):
                self._ctx.request_context.lifespan_context.file_watcher_service = None
            

    def record_error(self, error_message: str) -> None:
        """
        Record file watcher error in context for status reporting.

        Args:
            error_message: Error message to record
        """
        error_info = {
            'status': 'failed',
            'message': f'{error_message}. Auto-refresh disabled. Please use manual refresh.',
            'timestamp': time.time(),
            'manual_refresh_required': True
        }

        # Store error in context for status reporting
        if hasattr(self._ctx.request_context.lifespan_context, '__dict__'):
            self._ctx.request_context.lifespan_context.file_watcher_error = error_info

        
