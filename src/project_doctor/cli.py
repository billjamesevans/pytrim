from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze_project
from .context import AnalysisContext
from .dependencies import load_declared_dependencies
from .models import AnalysisReport, UvLockSummary
from .report import render_json, render_markdown, render_wow_report
from .utils import canonicalize_name
from .uv import locked_package_version, sync_check_uv, uv_summary_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-doctor",
        description="Analyze a Python project for import-time cost, dependency bloat, and lazy-import opportunities.",
    )
    parser.add_argument("--version", action="version", version=f"project-doctor {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run the default project health report.")
    _add_analysis_options(doctor)
    _add_report_options(doctor)

    analyze = subparsers.add_parser("analyze", help="Analyze a Python project.")
    _add_analysis_options(analyze)
    _add_report_options(analyze)

    check = subparsers.add_parser("check", help="Run CI-friendly optimization checks against a Python project.")
    _add_analysis_options(check)
    check.add_argument("--json", action="store_true", help="Emit machine-readable check results.")
    check.add_argument("--max-unused", type=int, default=0, help="Maximum likely unused dependencies. Default: 0.")
    check.add_argument("--max-undeclared", type=int, default=0, help="Maximum possible undeclared imports. Default: 0.")
    check.add_argument("--max-lazy-imports", type=int, help="Maximum lazy-import candidates.")
    check.add_argument("--max-import-ms", type=float, help="Maximum cumulative import time for any measured module.")
    check.add_argument(
        "--max-package-mb",
        type=float,
        help="Maximum installed package size for any declared dependency.",
    )

    sync_check = subparsers.add_parser("sync-check", help="Check pyproject.toml against uv.lock.")
    sync_check.add_argument("target", nargs="?", default="uv.lock", help="Path to uv.lock. Default: uv.lock.")
    sync_check.add_argument("--json", action="store_true", help="Emit machine-readable sync results.")

    explain = subparsers.add_parser("explain-package", help="Explain why a package matters to this project.")
    explain.add_argument("package", help="Distribution name to explain.")
    explain.add_argument("path", nargs="?", default=".", help="Project directory. Defaults to current directory.")
    explain.add_argument("--uv", action="store_true", help="Include uv.lock status when available.")
    explain.add_argument("--json", action="store_true", help="Emit machine-readable package explanation.")

    return parser


def _add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument(
        "--report",
        choices=("wow", "detailed"),
        default="wow",
        help="Human report style. Default: wow.",
    )
    parser.add_argument("--output", "-o", help="Write the report to a file instead of stdout.")


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project directory to analyze. Defaults to current directory.",
    )
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument(
        "--import-time",
        dest="import_time",
        action="store_true",
        help="Run subprocess import timing checks. This imports third-party modules in child processes.",
    )
    timing.add_argument(
        "--no-import-time",
        dest="import_time",
        action="store_false",
        help="Skip subprocess import timing checks. This is the default.",
    )
    parser.add_argument(
        "--import-time-limit",
        type=int,
        default=20,
        help="Maximum third-party modules to time. Default: 20.",
    )
    parser.add_argument(
        "--import-time-timeout",
        type=float,
        default=10.0,
        help="Timeout per module import in seconds. Default: 10.",
    )
    size_checks = parser.add_mutually_exclusive_group()
    size_checks.add_argument(
        "--package-sizes",
        dest="package_sizes",
        action="store_true",
        default=False,
        help="Collect installed package sizes for declared dependencies.",
    )
    size_checks.add_argument(
        "--no-package-sizes",
        dest="package_sizes",
        action="store_false",
        help="Skip installed package size checks. This is the default unless --max-package-mb is used.",
    )
    parser.add_argument(
        "--jobs",
        default="auto",
        help="Static scan worker count: a positive integer or 'auto'. Default: auto.",
    )
    parser.add_argument(
        "--entrypoint",
        help='Measure startup for a real entrypoint command, for example "python app.py" or "python -m my_cli".',
    )
    parser.add_argument(
        "--entrypoint-timeout",
        type=float,
        default=10.0,
        help="Timeout for entrypoint startup measurement in seconds. Default: 10.",
    )
    parser.add_argument("--uv", action="store_true", help="Include uv.lock status when uv.lock is present.")
    parser.add_argument("--max-files", type=int, default=5000, help="Maximum Python files to scan. Default: 5000.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional directory name to exclude. Can be repeated.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in {"analyze", "check", "doctor"}:
        try:
            report = _run_analysis(args)
        except Exception as exc:  # noqa: BLE001 - CLI should return a clear user-facing error.
            print(f"project-doctor: {exc}", file=sys.stderr)
            return 2

    if args.command in {"analyze", "doctor"}:
        if args.json:
            rendered = render_json(report)
        elif args.report == "detailed":
            rendered = render_markdown(report)
        else:
            rendered = render_wow_report(report)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0

    if args.command == "check":
        failures = _check_failures(report, args)
        if args.json:
            print(_render_check_json(report, failures))
        else:
            print(_render_check_text(report, failures), end="")
        return 1 if failures else 0

    if args.command == "sync-check":
        summary = sync_check_uv(Path(args.target))
        if args.json:
            print(json.dumps(uv_summary_to_dict(summary), indent=2, sort_keys=True))
        else:
            print(_render_uv_sync_text(summary), end="")
        return 0 if summary.status == "ok" else 1

    if args.command == "explain-package":
        explanation = _explain_package(args.package, Path(args.path), use_uv=args.uv)
        if args.json:
            print(json.dumps(explanation, indent=2, sort_keys=True))
        else:
            print(_render_package_explanation(explanation), end="")
        return 0

    parser.print_help()
    return 0


def _run_analysis(args: argparse.Namespace) -> AnalysisReport:
    collect_package_sizes = bool(args.package_sizes or getattr(args, "max_package_mb", None) is not None)
    return analyze_project(
        args.path,
        run_import_timing=args.import_time,
        import_time_limit=args.import_time_limit,
        import_time_timeout=args.import_time_timeout,
        max_files=args.max_files,
        excludes=args.exclude,
        collect_package_sizes=collect_package_sizes,
        jobs=args.jobs,
        entrypoint=args.entrypoint,
        entrypoint_timeout=args.entrypoint_timeout,
        use_uv=args.uv,
    )


def _check_failures(report: AnalysisReport, args: argparse.Namespace) -> list[str]:
    failures: list[str] = []

    unused_count = len(report.unused_dependencies)
    if args.max_unused is not None and unused_count > args.max_unused:
        failures.append(f"Likely unused dependencies: {unused_count} > {args.max_unused}")

    undeclared_count = len(report.undeclared_imports)
    if args.max_undeclared is not None and undeclared_count > args.max_undeclared:
        failures.append(f"Possible undeclared imports: {undeclared_count} > {args.max_undeclared}")

    lazy_count = len(report.lazy_import_candidates)
    if args.max_lazy_imports is not None and lazy_count > args.max_lazy_imports:
        failures.append(f"Lazy-import candidates: {lazy_count} > {args.max_lazy_imports}")

    if args.max_import_ms is not None:
        slow = [
            timing
            for timing in report.import_timings
            if timing.status == "ok" and timing.cumulative_ms is not None and timing.cumulative_ms > args.max_import_ms
        ]
        for timing in slow[:10]:
            failures.append(f"Import time for {timing.module}: {timing.cumulative_ms:g}ms > {args.max_import_ms:g}ms")
        if len(slow) > 10:
            failures.append(f"Import time failures truncated: {len(slow) - 10} more module(s)")

    if args.max_package_mb is not None:
        oversized = [
            size
            for size in report.package_sizes
            if size.status == "ok" and size.size_mb is not None and size.size_mb > args.max_package_mb
        ]
        for size in oversized[:10]:
            failures.append(f"Installed size for {size.distribution}: {size.size_mb:g}MB > {args.max_package_mb:g}MB")
        if len(oversized) > 10:
            failures.append(f"Package size failures truncated: {len(oversized) - 10} more package(s)")

    return failures


def _render_check_text(report: AnalysisReport, failures: list[str]) -> str:
    lines = [f"Project Doctor check {'failed' if failures else 'passed'}", ""]

    if failures:
        lines.append("Failures:")
        for failure in failures:
            lines.append(f"- {failure}")
        lines.append("")

    lines.append("Summary:")
    lines.append(f"- Python files scanned: {report.python_files_scanned}")
    lines.append(f"- Likely unused dependencies: {len(report.unused_dependencies)}")
    lines.append(f"- Possible undeclared imports: {len(report.undeclared_imports)}")
    lines.append(f"- Lazy-import candidates: {len(report.lazy_import_candidates)}")
    lines.append(f"- Import timing checks: {len(report.import_timings)}")
    lines.append(f"- Package size checks: {len(report.package_sizes)}")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings[:10]:
            lines.append(f"- {warning}")

    return "\n".join(lines).rstrip() + "\n"


def _render_check_json(report: AnalysisReport, failures: list[str]) -> str:
    payload = {
        "ok": not failures,
        "failures": failures,
        "summary": {
            "python_files_scanned": report.python_files_scanned,
            "unused_dependencies": len(report.unused_dependencies),
            "undeclared_imports": len(report.undeclared_imports),
            "lazy_import_candidates": len(report.lazy_import_candidates),
            "import_timing_checks": len(report.import_timings),
            "package_size_checks": len(report.package_sizes),
            "warnings": report.warnings,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _render_uv_sync_text(summary: UvLockSummary) -> str:
    status = "passed" if summary.status == "ok" else "failed"
    lines = [f"Project Doctor uv sync check {status}", ""]
    lines.append(f"- uv.lock: {summary.lock_path}")
    lines.append(f"- Locked packages: {summary.package_count}")
    lines.append(f"- Locked direct dependencies: {len(summary.locked_direct_dependencies)}")
    lines.append(f"- Missing direct dependencies: {len(summary.missing_direct_dependencies)}")
    if summary.missing_direct_dependencies:
        for name in summary.missing_direct_dependencies:
            lines.append(f"  - {name}")
    if summary.reason:
        lines.append(f"- Reason: {summary.reason}")
    return "\n".join(lines).rstrip() + "\n"


def _explain_package(package_name: str, project_root: Path, *, use_uv: bool) -> dict[str, object]:
    project_root = project_root.expanduser().resolve()
    dependencies, warnings = load_declared_dependencies(project_root)
    normalized = canonicalize_name(package_name)
    declared = [dep for dep in dependencies if dep.normalized_name == normalized]
    context = AnalysisContext.from_environment()
    import_names = context.installed_packages.import_names_for_distribution(package_name)
    size = context.package_size(package_name)
    payload: dict[str, object] = {
        "package": package_name,
        "project": str(project_root),
        "declared": bool(declared),
        "declarations": [
            {
                "source": dep.source,
                "raw": dep.raw,
            }
            for dep in declared
        ],
        "import_names": list(import_names),
        "installed_size_mb": size.size_mb,
        "installed_size_status": size.status,
        "warnings": warnings,
    }
    if use_uv:
        version = locked_package_version(project_root / "uv.lock", package_name)
        payload["uv_locked"] = version is not None
        payload["uv_version"] = version
    return payload


def _render_package_explanation(explanation: dict[str, object]) -> str:
    package = str(explanation["package"])
    lines = [f"Project Doctor package explanation: {package}", ""]
    lines.append(f"Declared: {'yes' if explanation['declared'] else 'no'}")
    declarations = explanation.get("declarations")
    if isinstance(declarations, list):
        for declaration in declarations:
            if isinstance(declaration, dict):
                lines.append(f"- {declaration.get('source')}: {declaration.get('raw')}")

    import_names = explanation.get("import_names")
    if isinstance(import_names, list):
        lines.append(f"Import names: {', '.join(str(name) for name in import_names)}")

    size = explanation.get("installed_size_mb")
    status = explanation.get("installed_size_status")
    if size is not None:
        lines.append(f"Installed size: {size}MB")
    else:
        lines.append(f"Installed size: {status}")

    if "uv_locked" in explanation:
        if explanation["uv_locked"]:
            lines.append(f"uv.lock: locked {explanation['uv_version']}")
        else:
            lines.append("uv.lock: not locked")

    warnings = explanation.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
