from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeclaredDependency:
    name: str
    normalized_name: str
    source: str
    raw: str


@dataclass(frozen=True)
class ImportRecord:
    file: str
    line: int
    module: str
    imported: str
    alias: str
    is_from_import: bool
    is_module_level: bool


@dataclass(frozen=True)
class PythonFileScan:
    file: str
    imports: tuple[ImportRecord, ...]
    import_time_uses: tuple[str, ...]
    deferred_uses: tuple[str, ...]
    syntax_error: str | None = None


@dataclass(frozen=True)
class LazyImportCandidate:
    file: str
    line: int
    module: str
    alias: str
    reason: str
    confidence: str = "medium"


@dataclass(frozen=True)
class DependencyUsage:
    dependency: str
    source: str
    status: str
    import_names: tuple[str, ...]
    reason: str
    confidence: str


@dataclass(frozen=True)
class ImportTiming:
    module: str
    self_ms: float | None
    cumulative_ms: float | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class PackageSize:
    distribution: str
    size_mb: float | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class EntrypointTiming:
    command: str
    status: str
    elapsed_ms: float | None
    returncode: int | None
    import_timings: list[ImportTiming] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class UvLockSummary:
    lock_path: str
    status: str
    package_count: int
    locked_direct_dependencies: tuple[str, ...] = ()
    missing_direct_dependencies: tuple[str, ...] = ()
    reason: str | None = None


@dataclass
class AnalysisReport:
    project_path: str
    python_files_scanned: int
    declared_dependencies: list[DeclaredDependency] = field(default_factory=list)
    imported_modules: list[str] = field(default_factory=list)
    third_party_imports: list[str] = field(default_factory=list)
    local_import_roots: list[str] = field(default_factory=list)
    dependency_usage: list[DependencyUsage] = field(default_factory=list)
    undeclared_imports: list[str] = field(default_factory=list)
    lazy_import_candidates: list[LazyImportCandidate] = field(default_factory=list)
    import_timings: list[ImportTiming] = field(default_factory=list)
    package_sizes: list[PackageSize] = field(default_factory=list)
    entrypoint: EntrypointTiming | None = None
    uv_lock: UvLockSummary | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def unused_dependencies(self) -> list[DependencyUsage]:
        return [item for item in self.dependency_usage if item.status == "unused"]

    @property
    def used_dependencies(self) -> list[DependencyUsage]:
        return [item for item in self.dependency_usage if item.status == "used"]


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
