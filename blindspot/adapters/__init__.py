"""Blindspot adapters — language and framework abstraction layer.

Adapters provide a uniform interface for language-specific and
framework-specific operations, allowing core intelligence tools
to work with any programming language or framework.
"""

from .project_structure import ProjectStructure, get_project_structure
from .language_syntax import LanguageSyntax, get_language_syntax
from .language_execution_adapter import LanguageExecutionAdapter, LanguageExecutionProfile
from .symbol_resolver import SymbolResolver

__all__ = [
    "ProjectStructure",
    "get_project_structure",
    "LanguageSyntax",
    "get_language_syntax",
    "LanguageExecutionAdapter",
    "LanguageExecutionProfile",
    "SymbolResolver",
]
