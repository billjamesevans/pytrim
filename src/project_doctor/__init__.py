"""Public API for Project Doctor."""

from .analyze import analyze_project
from .context import AnalysisContext
from .models import (
    AnalysisReport,
    DeclaredDependency,
    DependencyUsage,
    EntrypointTiming,
    ImportRecord,
    ImportTiming,
    LazyImportCandidate,
    PackageSize,
    PythonFileScan,
    UvLockSummary,
)

__version__ = "0.6.0"

__all__ = [
    "AnalysisContext",
    "AnalysisReport",
    "DeclaredDependency",
    "DependencyUsage",
    "EntrypointTiming",
    "ImportRecord",
    "ImportTiming",
    "LazyImportCandidate",
    "PackageSize",
    "PythonFileScan",
    "UvLockSummary",
    "__version__",
    "analyze_project",
]
