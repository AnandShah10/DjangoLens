"""DjangoLens static analyzers.

Importing this package does not auto-register analyzers; the CLI and
extension import the concrete modules they need. The formal Analyzer
protocol and registry live in `.base` for extensibility.
"""

from .base import Analyzer, AnalysisContext, BaseAnalyzer, register, all_analyzers, run_registered

__all__ = [
    "Analyzer",
    "AnalysisContext",
    "BaseAnalyzer",
    "register",
    "all_analyzers",
    "run_registered",
]

