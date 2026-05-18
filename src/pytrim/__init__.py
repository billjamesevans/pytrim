"""Public API for PyTrim."""

from .analyze import analyze_project
from .models import (
    AnalysisReport,
    DeclaredDependency,
    DependencyUsage,
    ImportRecord,
    ImportTiming,
    LazyImportCandidate,
    PackageSize,
    PythonFileScan,
)

__version__ = "0.2.1"

__all__ = [
    "AnalysisReport",
    "DeclaredDependency",
    "DependencyUsage",
    "ImportRecord",
    "ImportTiming",
    "LazyImportCandidate",
    "PackageSize",
    "PythonFileScan",
    "__version__",
    "analyze_project",
]
