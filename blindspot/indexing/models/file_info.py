"""
FileInfo model for representing file metadata.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any


@dataclass
class FileInfo:
    """Information about a source code file."""
    
    language: str  # programming language
    line_count: int  # total lines in file
    symbols: Dict[str, List[str]]  # symbol categories (functions, classes, etc.)
    imports: List[str]  # imported modules/packages
    exports: Optional[List[str]] = None  # exported symbols (for JS/TS modules)
    package: Optional[str] = None  # package name (for Java, Go, etc.)
    docstring: Optional[str] = None  # file-level documentation
    
    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.exports is None:
            self.exports = []