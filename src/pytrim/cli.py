from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze_project
from .models import AnalysisReport
from .report import render_json, render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pytrim",
        description="Analyze a Python project for import-time cost, dependency bloat, and lazy-import opportunities.",
    )
    parser.add_argument("--version", action="version", version=f"pytrim {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    analyze = subparsers.add_parser("analyze", help="Analyze a Python project.")
    _add_analysis_options(analyze)
    analyze.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    analyze.add_argument("--output", "-o", help="Write the report to a file instead of stdout.")

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

    return parser


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

    if args.command in {"analyze", "check"}:
        try:
            report = _run_analysis(args)
        except Exception as exc:  # noqa: BLE001 - CLI should return a clear user-facing error.
            print(f"pytrim: {exc}", file=sys.stderr)
            return 2

    if args.command == "analyze":
        rendered = render_json(report) if args.json else render_markdown(report)
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

    parser.print_help()
    return 0


def _run_analysis(args: argparse.Namespace) -> AnalysisReport:
    return analyze_project(
        args.path,
        run_import_timing=args.import_time,
        import_time_limit=args.import_time_limit,
        import_time_timeout=args.import_time_timeout,
        max_files=args.max_files,
        excludes=args.exclude,
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
    lines = [f"PyTrim check {'failed' if failures else 'passed'}", ""]

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
            "warnings": report.warnings,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
