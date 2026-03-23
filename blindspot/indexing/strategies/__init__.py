"""
Parsing strategies for different programming languages.
"""

from .base_strategy import ParsingStrategy
from .strategy_factory import StrategyFactory

__all__ = ['ParsingStrategy', 'StrategyFactory']