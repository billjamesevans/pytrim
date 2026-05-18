from __future__ import annotations

import json
from collections.abc import Iterable

from .models import AnalysisReport, ImportTiming, PackageSize


def render_json(report: AnalysisReport, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)


def render_markdown(report: AnalysisReport) -> str:
    lines: list[str] = []
    lines.append("# PyTrim Optimization Report")
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
        "PyTrim is conservative. Static analysis can miss dynamic imports, plugin systems, "
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
