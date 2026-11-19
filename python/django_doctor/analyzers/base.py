"""Formal Analyzer interface and registry.

New analyzers should implement the Analyzer protocol (or subclass BaseAnalyzer)
and register themselves. The CLI and extension invoke analyzers through the
registry so additional checks can be added without rewriting the scan loop.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class Analyzer(Protocol):
    """Minimal contract every static analyzer must satisfy."""

    id: str
    name: str
    description: str

    def supported(self, project_root: str, apps: list) -> bool:
        """Return True if this analyzer can usefully run on the project."""
        ...

    def analyze(self, context: "AnalysisContext") -> List[Dict[str, Any]]:
        """Run analysis and return a list of diagnostic dicts."""
        ...


class AnalysisContext:
    """Shared context passed to every analyzer during a scan."""

    def __init__(
        self,
        project_root: str,
        apps: list,
        config: Optional[dict] = None,
        import_records: Optional[list] = None,
        iter_python_files: Optional[Callable] = None,
    ):
        self.project_root = project_root
        self.apps = apps  # list of (name, path)
        self.config = config or {}
        self.import_records = import_records or []
        self.iter_python_files = iter_python_files


class BaseAnalyzer:
    """Convenience base with sensible defaults."""

    id: str = "base"
    name: str = "Base"
    description: str = ""

    def supported(self, project_root: str, apps: list) -> bool:
        return bool(apps)

    def analyze(self, context: AnalysisContext) -> List[Dict[str, Any]]:
        raise NotImplementedError


# Global registry populated by register() and by import side-effects.
_REGISTRY: Dict[str, Analyzer] = {}


def register(analyzer: Analyzer) -> Analyzer:
    """Register an analyzer instance. Returns it so it can be used as a decorator."""
    _REGISTRY[analyzer.id] = analyzer
    return analyzer


def get_analyzer(analyzer_id: str) -> Optional[Analyzer]:
    return _REGISTRY.get(analyzer_id)


def all_analyzers() -> List[Analyzer]:
    return list(_REGISTRY.values())


def run_registered(context: AnalysisContext, only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Run all (or a subset of) registered analyzers and concatenate diagnostics."""
    diagnostics: List[Dict[str, Any]] = []
    for analyzer in all_analyzers():
        if only is not None and analyzer.id not in only:
            continue
        if not analyzer.supported(context.project_root, context.apps):
            continue
        try:
            diagnostics.extend(analyzer.analyze(context) or [])
        except Exception:
            # A single broken analyzer must never crash the whole scan.
            continue
    return diagnostics
