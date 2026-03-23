"""
Index provider interface definitions.

Defines standard interfaces for all index access, ensuring consistency across different implementations.
"""

from typing import List, Optional, Dict, Any, Protocol
from dataclasses import dataclass

from .models import SymbolInfo, FileInfo


@dataclass
class IndexMetadata:
    """Standard index metadata structure."""
    version: str
    format_type: str
    created_at: float
    last_updated: float
    file_count: int
    project_root: str
    tool_version: str


class IIndexProvider(Protocol):
    """
    Standard index provider interface.
    
    All index implementations must follow this interface to ensure consistent access patterns.
    """
    
    def get_file_list(self) -> List[FileInfo]:
        """
        Get list of all indexed files.
        
        Returns:
            List of file information objects
        """
        ...
    
    def get_file_info(self, file_path: str) -> Optional[FileInfo]:
        """
        Get information for a specific file.
        
        Args:
            file_path: Relative file path
            
        Returns:
            File information, or None if file is not in index
        """
        ...
    
    def query_symbols(self, file_path: str) -> List[SymbolInfo]:
        """
        Query symbol information in a file.
        
        Args:
            file_path: Relative file path
            
        Returns:
            List of symbol information objects
        """
        ...
    
    def search_files(self, pattern: str) -> List[str]:
        """
        Search files by pattern.
        
        Args:
            pattern: Glob pattern or regular expression
            
        Returns:
            List of matching file paths
        """
        ...
    
    def get_metadata(self) -> IndexMetadata:
        """
        Get index metadata.
        
        Returns:
            Index metadata information
        """
        ...
    
    def is_available(self) -> bool:
        """
        Check if index is available.
        
        Returns:
            True if index is available and functional
        """
        ...


class IIndexManager(Protocol):
    """
    Index manager interface.
    
    Defines standard interface for index lifecycle management.
    """
    
    def initialize(self) -> bool:
        """Initialize the index manager."""
        ...
    
    def get_provider(self) -> Optional[IIndexProvider]:
        """Get the current active index provider."""
        ...
    
    def refresh_index(self, force: bool = False) -> bool:
        """Refresh the index."""
        ...
    
    def save_index(self) -> bool:
        """Save index state."""
        ...
    
    def clear_index(self) -> None:
        """Clear index state."""
        ...
    
    def get_index_status(self) -> Dict[str, Any]:
        """Get index status information."""
        ...
