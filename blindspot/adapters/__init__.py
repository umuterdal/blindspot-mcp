"""Core adapters used by the context engine."""

from .project_structure import ProjectStructure, get_project_structure
from .language_syntax import LanguageSyntax, get_language_syntax
from .symbol_resolver import SymbolResolver

__all__ = [
    "ProjectStructure",
    "get_project_structure",
    "LanguageSyntax",
    "get_language_syntax",
    "SymbolResolver",
]
