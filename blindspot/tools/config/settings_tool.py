"""
Settings Tool - Pure technical component for settings operations.

This tool handles low-level settings operations without any business logic.
"""

import os
import tempfile
from typing import Dict, Any

from ...constants import SETTINGS_DIR


class SettingsTool:
    """
    Pure technical component for settings operations.

    This tool provides low-level settings management capabilities
    without any business logic or decision making.
    """

    def __init__(self):
        pass

    def get_temp_directory_path(self) -> str:
        """
        Get the path to the temporary directory for settings.

        Returns:
            Path to the temporary settings directory
        """
        return os.path.join(tempfile.gettempdir(), SETTINGS_DIR)

    def create_temp_directory(self) -> Dict[str, Any]:
        """
        Create the temporary directory for settings.

        Returns:
            Dictionary with creation results
        """
        temp_dir = self.get_temp_directory_path()
        existed_before = os.path.exists(temp_dir)

        try:
            os.makedirs(temp_dir, exist_ok=True)

            return {
                "temp_directory": temp_dir,
                "exists": os.path.exists(temp_dir),
                "is_directory": os.path.isdir(temp_dir),
                "existed_before": existed_before,
                "created": not existed_before
            }

        except (OSError, IOError) as e:
            return {
                "temp_directory": temp_dir,
                "exists": False,
                "error": str(e)
            }

    def check_temp_directory(self) -> Dict[str, Any]:
        """
        Check the status of the temporary directory.

        Returns:
            Dictionary with directory status information
        """
        temp_dir = self.get_temp_directory_path()

        result = {
            "temp_directory": temp_dir,
            "temp_root": tempfile.gettempdir(),
            "exists": os.path.exists(temp_dir),
            "is_directory": os.path.isdir(temp_dir) if os.path.exists(temp_dir) else False
        }

        # If the directory exists, list its contents
        if result["exists"] and result["is_directory"]:
            try:
                contents = os.listdir(temp_dir)
                result["contents"] = contents
                result["subdirectories"] = []

                # Check each subdirectory
                for item in contents:
                    item_path = os.path.join(temp_dir, item)
                    if os.path.isdir(item_path):
                        subdir_info = {
                            "name": item,
                            "path": item_path,
                            "contents": os.listdir(item_path) if os.path.exists(item_path) else []
                        }
                        result["subdirectories"].append(subdir_info)

            except (OSError, PermissionError) as e:
                result["error"] = str(e)

        return result

