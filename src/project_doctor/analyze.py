from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .context import AnalysisContext
from .dependencies import dependency_usage_status, load_declared_dependencies, undeclared_imports
from .entrypoint import measure_entrypoint_startup
from .import_timing import measure_import_times
from .models import AnalysisReport, EntrypointTiming, ImportTiming, LazyImportCandidate, PackageSize, PythonFileScan
from .static_scan import ScanJobs, infer_local_import_roots, iter_lazy_import_candidates, iter_scan_python_files
from .utils import is_stdlib_module, iter_python_files, top_import_name
from .uv import sync_check_uv


@dataclass
class _ScanSummary:
    warnings: list[str]
    all_imports: set[str]
    lazy_import_candidates: list[LazyImportCandidate]


def analyze_project(
    path: str | Path,
    *,
    run_import_timing: bool = False,
    import_time_limit: int = 20,
    import_time_timeout: float = 10.0,
    max_files: int = 5000,
    excludes: Iterable[str] = (),
    collect_package_sizes: bool = False,
    context: AnalysisContext | None = None,
    jobs: ScanJobs = "auto",
    entrypoint: str | None = None,
    entrypoint_timeout: float = 10.0,
    use_uv: bool = False,
) -> AnalysisReport:
    project_root = Path(path).expanduser().resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project path does not exist: {project_root}")
    if project_root.is_file():
        project_root = project_root.parent

    declared_dependencies, dep_warnings = load_declared_dependencies(project_root)
    python_files, file_warnings = iter_python_files(project_root, max_files=max_files, extra_excludes=excludes)
    summary = _collect_scan_summary(
        iter_scan_python_files(python_files, project_root, jobs=jobs),
    )

    warnings = [*dep_warnings, *file_warnings, *summary.warnings]
    all_imports = sorted(summary.all_imports)
    local_roots = infer_local_import_roots(project_root, python_files)
    analysis_context = context or AnalysisContext.from_environment()

    third_party = sorted(
        name
        for name in all_imports
        if not is_stdlib_module(name)
        and top_import_name(name) not in local_roots
        and not name.startswith("__")
    )
    third_party_set = set(third_party)

    usage = dependency_usage_status(declared_dependencies, set(all_imports), analysis_context.installed_packages)
    missing = undeclared_imports(third_party, declared_dependencies, analysis_context.installed_packages)

    timings: list[ImportTiming] = []
    if run_import_timing and third_party:
        timings = measure_import_times(
            third_party,
            limit=import_time_limit,
            timeout_seconds=import_time_timeout,
        )
    elif not run_import_timing:
        warnings.append("Import timing disabled by default. Use --import-time to enable subprocess timing checks.")

    entrypoint_timing: EntrypointTiming | None = None
    if entrypoint is not None:
        entrypoint_timing = measure_entrypoint_startup(
            entrypoint,
            cwd=project_root,
            timeout_seconds=entrypoint_timeout,
        )
        if entrypoint_timing.status != "ok" and entrypoint_timing.reason:
            warnings.append(f"Entrypoint timing {entrypoint_timing.status}: {entrypoint_timing.reason}")

    sizes: list[PackageSize] = []
    if collect_package_sizes:
        sizes = [analysis_context.package_size(dep.name) for dep in declared_dependencies]
    else:
        warnings.append("Package size checks disabled by default. Use --package-sizes to enable installed size checks.")

    heavy_by_time = {
        item.module
        for item in [*timings, *(entrypoint_timing.import_timings if entrypoint_timing else [])]
        if item.status == "ok" and item.cumulative_ms is not None and item.cumulative_ms >= 150
    }
    heavy_by_size = _heavy_import_names_from_sizes(sizes, analysis_context)
    lazy_candidates = [
        _promote_lazy_candidate(item, heavy_modules=heavy_by_time | heavy_by_size)
        for item in summary.lazy_import_candidates
        if item.module in third_party_set
    ]

    uv_lock = sync_check_uv(project_root / "uv.lock") if use_uv else None
    if uv_lock is not None and uv_lock.status != "ok" and uv_lock.reason:
        warnings.append(f"uv.lock {uv_lock.status}: {uv_lock.reason}")

    return AnalysisReport(
        project_path=str(project_root),
        python_files_scanned=len(python_files),
        declared_dependencies=declared_dependencies,
        imported_modules=all_imports,
        third_party_imports=third_party,
        local_import_roots=sorted(local_roots),
        dependency_usage=usage,
        undeclared_imports=missing,
        lazy_import_candidates=lazy_candidates,
        import_timings=timings,
        package_sizes=sorted(
            sizes,
            key=lambda item: (
                item.status != "ok",
                -1 if item.size_mb is None else -item.size_mb,
                item.distribution.lower(),
            ),
        ),
        entrypoint=entrypoint_timing,
        uv_lock=uv_lock,
        warnings=warnings,
    )


def _collect_scan_summary(scans: Iterable[PythonFileScan]) -> _ScanSummary:
    warnings: list[str] = []
    all_imports: set[str] = set()
    lazy_import_candidates: list[LazyImportCandidate] = []
    for scan in scans:
        if scan.syntax_error:
            warnings.append(f"{scan.file}: {scan.syntax_error}")
        for record in scan.imports:
            all_imports.add(record.module)
        lazy_import_candidates.extend(iter_lazy_import_candidates((scan,)))
    return _ScanSummary(
        warnings=warnings,
        all_imports=all_imports,
        lazy_import_candidates=sorted(
            lazy_import_candidates,
            key=lambda item: (item.file, item.line, item.module, item.alias),
        ),
    )


def _heavy_import_names_from_sizes(sizes: Iterable[PackageSize], context: AnalysisContext) -> set[str]:
    heavy: set[str] = set()
    for item in sizes:
        if item.status != "ok" or item.size_mb is None or item.size_mb < 20:
            continue
        heavy.update(context.installed_packages.import_names_for_distribution(item.distribution))
    return heavy


def _promote_lazy_candidate(item: LazyImportCandidate, heavy_modules: set[str]) -> LazyImportCandidate:
    if item.module not in heavy_modules or item.confidence == "high":
        return item
    return replace(
        item,
        reason=f"{item.reason} The module also appears costly by import time or installed size.",
        confidence="high",
    )
