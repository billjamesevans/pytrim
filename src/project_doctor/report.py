from __future__ import annotations

import json
from collections.abc import Iterable

from .models import AnalysisReport, ImportTiming, PackageSize


def render_json(report: AnalysisReport, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)


def render_wow_report(report: AnalysisReport) -> str:
    lines: list[str] = ["# Project Doctor Report", ""]
    lines.append(f"Project: `{report.project_path}`")
    lines.append(f"Python files scanned: {report.python_files_scanned}")
    if report.entrypoint:
        elapsed = _duration(report.entrypoint.elapsed_ms)
        suffix = f" ({report.entrypoint.status})" if report.entrypoint.status != "ok" else ""
        lines.append(f"Entrypoint startup: {elapsed}{suffix}")
        movable_drag = _movable_entrypoint_drag(report)
        if movable_drag is not None:
            lines.append(f"Potential default-path import drag: {_duration(movable_drag)}")
        lines.append(f"Entrypoint: `{report.entrypoint.command}`")
    if report.uv_lock:
        lines.append(f"uv.lock: {report.uv_lock.status} ({report.uv_lock.package_count} packages)")
    lines.append("")

    lines.append("## Startup drag")
    startup_drag = _startup_drag(report)
    if startup_drag:
        for timing in startup_drag[:8]:
            lines.append(f"- {timing.module}: {_ms(timing.cumulative_ms)}")
    else:
        lines.append("- No import timing data collected. Use `--import-time` or `--entrypoint`.")
    lines.append("")

    lines.append("## Likely unused dependencies")
    if report.unused_dependencies:
        for dependency in report.unused_dependencies[:12]:
            lines.append(f"- {dependency.dependency}")
    else:
        lines.append("- None found.")
    lines.append("")

    lines.append("## Possible undeclared imports")
    if report.undeclared_imports:
        for name in report.undeclared_imports[:12]:
            lines.append(f"- {name} imported but not declared")
    else:
        lines.append("- None found.")
    lines.append("")

    lines.append("## Largest installed packages")
    largest = [item for item in report.package_sizes if item.status == "ok" and item.size_mb is not None]
    if largest:
        for package in sorted(largest, key=lambda size: -1 if size.size_mb is None else -size.size_mb)[:8]:
            lines.append(f"- {package.distribution}: {_mb(package.size_mb)}")
    else:
        lines.append("- Package sizes not collected. Use `--package-sizes`.")
    lines.append("")

    lines.append("## Suggested quick wins")
    quick_wins = _quick_wins(report)
    if quick_wins:
        for index, action in enumerate(quick_wins[:8], start=1):
            lines.append(f"{index}. {action}")
    else:
        lines.append("1. No obvious high-impact cleanup was found in this pass.")
    lines.append("")

    lines.append("## CI copy/paste")
    lines.append("")
    lines.append("```yaml")
    lines.append("- name: Check Python dependency health")
    lines.append("  run: project-doctor check . --max-unused 0 --max-undeclared 0 --max-package-mb 100")
    lines.append("```")
    lines.append("")
    lines.append("![Project Doctor](https://img.shields.io/badge/project--doctor-passing-brightgreen)")

    return "\n".join(lines).rstrip() + "\n"


def render_markdown(report: AnalysisReport) -> str:
    lines: list[str] = []
    lines.append("# Project Doctor Optimization Report")
    lines.append("")
    lines.append(f"**Project:** `{report.project_path}`")
    lines.append(f"**Python files scanned:** {report.python_files_scanned}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(_summary_table(report))
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Best next actions")
    lines.append("")
    actions = _best_actions(report)
    if actions:
        for action in actions:
            lines.append(f"- {action}")
    else:
        lines.append("- No obvious high-impact cleanup was found in this pass.")
    lines.append("")

    lines.append("## Slow imports")
    lines.append("")
    ok_timings = [item for item in report.import_timings if item.status == "ok"]
    if ok_timings:
        lines.extend(_timing_table(ok_timings[:15]))
    else:
        lines.append(
            "No successful import timings were collected. Use `--import-time` "
            "to enable subprocess timing checks."
        )
    failed_timings = [item for item in report.import_timings if item.status != "ok"]
    if failed_timings:
        lines.append("")
        lines.append("Import timing failures:")
        for timing in failed_timings[:10]:
            reason = f" — {timing.reason}" if timing.reason else ""
            lines.append(f"- `{timing.module}`: {timing.status}{reason}")
    lines.append("")

    lines.append("## Dependency usage")
    lines.append("")
    if report.dependency_usage:
        lines.extend(_dependency_table(report))
    else:
        lines.append("No declared dependencies were found in `pyproject.toml` or `requirements*.txt`.")
    lines.append("")

    if report.undeclared_imports:
        lines.append("## Possible undeclared imports")
        lines.append("")
        lines.append("These imports looked third-party but were not matched to declared dependencies:")
        for name in report.undeclared_imports[:25]:
            lines.append(f"- `{name}`")
        lines.append("")

    lines.append("## Lazy-import candidates")
    lines.append("")
    if report.lazy_import_candidates:
        lines.extend(_lazy_table(report))
        lines.append("")
        lines.append("Example manual fix:")
        lines.append("")
        lines.append("```python")
        lines.append("# Before")
        lines.append("import pandas as pd")
        lines.append("")
        lines.append("def make_report(...):")
        lines.append("    return pd.DataFrame(...)")
        lines.append("")
        lines.append("# After")
        lines.append("def make_report(...):")
        lines.append("    import pandas as pd")
        lines.append("    return pd.DataFrame(...)")
        lines.append("```")
    else:
        lines.append("No safe-looking lazy-import candidates were found.")
    lines.append("")

    lines.append("## Installed package sizes")
    lines.append("")
    ok_sizes = [item for item in report.package_sizes if item.status == "ok" and item.size_mb is not None]
    if ok_sizes:
        lines.extend(_size_table(ok_sizes[:15]))
    elif not report.package_sizes:
        lines.append("Package size checks were not collected. Use `--package-sizes` to enable them.")
    else:
        lines.append("No installed package sizes were available for declared dependencies in this environment.")
    unavailable_sizes = [item for item in report.package_sizes if item.status != "ok"]
    if unavailable_sizes:
        lines.append("")
        lines.append("Unavailable size checks:")
        for size in unavailable_sizes[:10]:
            reason = f" — {size.reason}" if size.reason else ""
            lines.append(f"- `{size.distribution}`: {size.status}{reason}")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "Project Doctor is conservative. Static analysis can miss dynamic imports, plugin systems, "
        "optional dependencies, and code paths loaded through strings. Treat `unused` as a "
        "review queue, not an automatic delete list."
    )

    return "\n".join(lines).rstrip() + "\n"


def _summary_table(report: AnalysisReport) -> str:
    rows = [
        ("Declared dependencies", len(report.declared_dependencies)),
        ("Imported top-level modules", len(report.imported_modules)),
        ("Likely third-party imports", len(report.third_party_imports)),
        ("Likely unused dependencies", len(report.unused_dependencies)),
        ("Possible undeclared imports", len(report.undeclared_imports)),
        ("Lazy-import candidates", len(report.lazy_import_candidates)),
    ]
    lines = ["| Metric | Count |", "|---|---:|"]
    for name, count in rows:
        lines.append(f"| {name} | {count} |")
    return "\n".join(lines)


def _best_actions(report: AnalysisReport) -> list[str]:
    actions: list[str] = []
    slow = [
        item
        for item in report.import_timings
        if item.status == "ok" and item.cumulative_ms and item.cumulative_ms >= 150
    ]
    if slow:
        modules = ", ".join(f"`{item.module}` ({item.cumulative_ms:g}ms)" for item in slow[:3])
        actions.append(f"Review slow imports first: {modules}.")

    if report.lazy_import_candidates:
        first = report.lazy_import_candidates[0]
        actions.append(
            f"Try a manual lazy import in `{first.file}:{first.line}` for `{first.module}` "
            "and rerun your startup benchmark."
        )

    unused = report.unused_dependencies
    if unused:
        modules = ", ".join(f"`{item.dependency}`" for item in unused[:5])
        actions.append(f"Review likely unused dependencies: {modules}.")

    big = [item for item in report.package_sizes if item.status == "ok" and item.size_mb and item.size_mb >= 20]
    if big:
        modules = ", ".join(f"`{item.distribution}` ({item.size_mb:g}MB)" for item in big[:3])
        actions.append(f"Audit large installed dependencies: {modules}.")

    return actions[:6]


def _startup_drag(report: AnalysisReport) -> list[ImportTiming]:
    timings = report.import_timings
    if not timings and report.entrypoint:
        third_party = set(report.third_party_imports)
        timings = [
            item
            for item in report.entrypoint.import_timings
            if item.module in third_party or item.module.split(".", 1)[0] in third_party
        ]
    return sorted(
        [item for item in timings if item.status == "ok" and item.cumulative_ms is not None],
        key=lambda item: (-1 if item.cumulative_ms is None else -item.cumulative_ms, item.module),
    )


def _movable_entrypoint_drag(report: AnalysisReport) -> float | None:
    if not report.entrypoint:
        return None
    movable_modules = {candidate.module for candidate in report.lazy_import_candidates}
    if not movable_modules:
        return None
    total = 0.0
    for timing in report.entrypoint.import_timings:
        if timing.status != "ok" or timing.cumulative_ms is None:
            continue
        if timing.module in movable_modules or timing.module.split(".", 1)[0] in movable_modules:
            total += timing.cumulative_ms
    if total <= 0:
        return None
    if report.entrypoint.elapsed_ms is not None:
        total = min(total, report.entrypoint.elapsed_ms)
    return round(total, 3)


def _quick_wins(report: AnalysisReport) -> list[str]:
    actions: list[str] = []
    for candidate in report.lazy_import_candidates[:3]:
        actions.append(
            f"Move `{candidate.module}` import at `{candidate.file}:{candidate.line}` "
            "inside the deferred function that uses it."
        )
    for dependency in report.unused_dependencies[:3]:
        actions.append(f"Remove `{dependency.dependency}` if it is no longer used by active code paths.")
    for name in report.undeclared_imports[:3]:
        actions.append(f"Add `{name}` to `pyproject.toml`.")
    if report.uv_lock and report.uv_lock.missing_direct_dependencies:
        missing = ", ".join(f"`{name}`" for name in report.uv_lock.missing_direct_dependencies[:3])
        actions.append(f"Run `uv lock` or `uv sync` so uv.lock includes {missing}.")
    return actions


def _timing_table(timings: Iterable[ImportTiming]) -> list[str]:
    lines = ["| Module | Cumulative import time | Self time |", "|---|---:|---:|"]
    for item in timings:
        lines.append(f"| `{item.module}` | {_ms(item.cumulative_ms)} | {_ms(item.self_ms)} |")
    return lines


def _dependency_table(report: AnalysisReport) -> list[str]:
    lines = ["| Dependency | Status | Matched imports | Confidence | Source |", "|---|---|---|---|---|"]
    for item in sorted(report.dependency_usage, key=lambda i: (i.status != "unused", i.dependency.lower()))[:50]:
        imports = ", ".join(f"`{name}`" for name in item.import_names) or "—"
        lines.append(f"| `{item.dependency}` | {item.status} | {imports} | {item.confidence} | {item.source} |")
    return lines


def _lazy_table(report: AnalysisReport) -> list[str]:
    lines = ["| File | Line | Module | Alias | Confidence |", "|---|---:|---|---|---|"]
    for item in report.lazy_import_candidates[:50]:
        lines.append(f"| `{item.file}` | {item.line} | `{item.module}` | `{item.alias}` | {item.confidence} |")
    return lines


def _size_table(sizes: Iterable[PackageSize]) -> list[str]:
    lines = ["| Distribution | Installed size |", "|---|---:|"]
    for item in sorted(sizes, key=lambda i: -1 if i.size_mb is None else -i.size_mb):
        lines.append(f"| `{item.distribution}` | {item.size_mb:g}MB |")
    return lines


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}ms"


def _mb(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}MB"


def _duration(value_ms: float | None) -> str:
    if value_ms is None:
        return "unknown"
    if value_ms >= 1000:
        return f"{value_ms / 1000:g}s"
    return f"{value_ms:g}ms"
