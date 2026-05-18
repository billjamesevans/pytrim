from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .dependencies import (
    dependency_usage_status,
    estimate_distribution_size,
    installed_import_name_map,
    load_declared_dependencies,
    undeclared_imports,
)
from .import_timing import measure_import_times
from .models import AnalysisReport
from .static_scan import find_lazy_import_candidates, infer_local_import_roots, scan_python_files
from .utils import is_stdlib_module, iter_python_files, top_import_name


def analyze_project(
    path: str | Path,
    *,
    run_import_timing: bool = False,
    import_time_limit: int = 20,
    import_time_timeout: float = 10.0,
    max_files: int = 5000,
    excludes: Iterable[str] = (),
) -> AnalysisReport:
    project_root = Path(path).expanduser().resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project path does not exist: {project_root}")
    if project_root.is_file():
        project_root = project_root.parent

    declared_dependencies, dep_warnings = load_declared_dependencies(project_root)
    python_files, file_warnings = iter_python_files(project_root, max_files=max_files, extra_excludes=excludes)
    scans = scan_python_files(python_files, project_root)

    warnings = [*dep_warnings, *file_warnings]
    for scan in scans:
        if scan.syntax_error:
            warnings.append(f"{scan.file}: {scan.syntax_error}")

    all_imports = sorted({record.module for scan in scans for record in scan.imports})
    local_roots = infer_local_import_roots(project_root, python_files)
    import_to_dist = installed_import_name_map()

    third_party = sorted(
        name
        for name in all_imports
        if not is_stdlib_module(name)
        and top_import_name(name) not in local_roots
        and not name.startswith("__")
    )

    usage = dependency_usage_status(declared_dependencies, set(all_imports), import_to_dist)
    missing = undeclared_imports(third_party, declared_dependencies, import_to_dist)

    timings = []
    if run_import_timing and third_party:
        timings = measure_import_times(
            third_party,
            limit=import_time_limit,
            timeout_seconds=import_time_timeout,
        )
    elif not run_import_timing:
        warnings.append("Import timing disabled by default. Use --import-time to enable subprocess timing checks.")

    sizes = [estimate_distribution_size(dep.name) for dep in declared_dependencies]
    heavy_by_time = {
        item.module
        for item in timings
        if item.status == "ok" and item.cumulative_ms is not None and item.cumulative_ms >= 150
    }
    heavy_by_size = {
        dep.distribution.replace("-", "_")
        for dep in sizes
        if dep.status == "ok" and dep.size_mb is not None and dep.size_mb >= 20
    }
    lazy_candidates = [
        item
        for item in find_lazy_import_candidates(scans, heavy_modules=heavy_by_time | heavy_by_size)
        if item.module in set(third_party)
    ]

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
        warnings=warnings,
    )
