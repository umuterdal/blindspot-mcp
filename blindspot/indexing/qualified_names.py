"""
Qualified name generation utilities.
"""
import os
from typing import Optional


def normalize_file_path(file_path: str) -> str:
    """
    Normalize a file path to use forward slashes and relative paths.
    
    Args:
        file_path: The file path to normalize
        
    Returns:
        Normalized file path
    """
    # Convert to forward slashes and make relative
    normalized = file_path.replace('\\', '/')
    
    # Remove leading slash if present
    if normalized.startswith('/'):
        normalized = normalized[1:]
    
    return normalized


def generate_qualified_name(file_path: str, symbol_name: str, namespace: Optional[str] = None) -> str:
    """
    Generate a qualified name for a symbol.
    
    Args:
        file_path: Path to the file containing the symbol
        symbol_name: Name of the symbol
        namespace: Optional namespace/module context
        
    Returns:
        Qualified name for the symbol
    """
    normalized_path = normalize_file_path(file_path)
    
    # Remove file extension for module-like name
    base_name = os.path.splitext(normalized_path)[0]
    module_path = base_name.replace('/', '.')
    
    if namespace:
        return f"{module_path}.{namespace}.{symbol_name}"
    else:
        return f"{module_path}.{symbol_name}"