"""
File Service - Simple file reading service for MCP resources.

This service provides simple file content reading functionality for MCP resources.
Complex file analysis has been moved to CodeIntelligenceService.

Usage:
- get_file_content() - used by files://{file_path} resource
"""

import os
from .base_service import BaseService


class FileService(BaseService):
    """
    Simple service for file content reading.

    This service handles basic file reading operations for MCP resources.
    Complex analysis functionality has been moved to CodeIntelligenceService.
    """

    def get_file_content(self, file_path: str) -> str:
        """
        Get file content for MCP resource.

        Args:
            file_path: Path to the file (relative to project root)

        Returns:
            File content as string

        Raises:
            ValueError: If project is not set up or path is invalid
            FileNotFoundError: If file is not found or readable
        """
        self._require_project_setup()
        self._require_valid_file_path(file_path)

        # Build full path
        full_path = os.path.join(self.base_path, file_path)

        try:
            # Try UTF-8 first (most common)
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # Try other encodings if UTF-8 fails
            encodings = ['utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1']
            for encoding in encodings:
                try:
                    with open(full_path, 'r', encoding=encoding) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue

            raise ValueError(
                f"Could not decode file {file_path}. File may have "
                f"unsupported encoding."
            ) from None
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise FileNotFoundError(f"Error reading file: {e}") from e
