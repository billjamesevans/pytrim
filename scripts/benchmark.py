#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from project_doctor import AnalysisContext, analyze_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Project Doctor analysis throughput.")
    parser.add_argument(
        "project",
        nargs="?",
        help="Existing project to benchmark. If omitted, a synthetic project is generated.",
    )
    parser.add_argument("--files", type=int, default=500, help="Synthetic Python file count. Default: 500.")
    parser.add_argument("--runs", type=int, default=5, help="Number of analysis runs. Default: 5.")
    parser.add_argument("--jobs", default="auto", help="Static scan worker count: positive integer or 'auto'.")
    parser.add_argument("--package-sizes", action="store_true", help="Include installed package size checks.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if args.files < 1:
        raise SystemExit("--files must be at least 1")

    if args.project:
        project_root = Path(args.project).expanduser().resolve()
        payload = _benchmark_project(project_root, args)
    else:
        with tempfile.TemporaryDirectory(prefix="project-doctor-benchmark-") as temp_dir:
            project_root = Path(temp_dir)
            _write_synthetic_project(project_root, args.files)
            payload = _benchmark_project(project_root, args)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _benchmark_project(project_root: Path, args: argparse.Namespace) -> dict[str, object]:
    context = AnalysisContext.from_environment()
    elapsed: list[float] = []
    files_scanned = 0
    for _ in range(args.runs):
        start = time.perf_counter()
        report = analyze_project(
            project_root,
            run_import_timing=False,
            collect_package_sizes=args.package_sizes,
            context=context,
            jobs=args.jobs,
            max_files=max(args.files, 1_000_000),
        )
        elapsed.append(time.perf_counter() - start)
        files_scanned = report.python_files_scanned

    return {
        "project": str(project_root),
        "files": files_scanned,
        "requested_files": args.files,
        "runs": args.runs,
        "jobs": str(args.jobs),
        "package_sizes": bool(args.package_sizes),
        "min_seconds": round(min(elapsed), 6),
        "median_seconds": round(statistics.median(elapsed), 6),
        "max_seconds": round(max(elapsed), 6),
        "files_per_second": round(files_scanned / statistics.median(elapsed), 2) if elapsed else 0,
    }


def _write_synthetic_project(project_root: Path, files: int) -> None:
    package = project_root / "src" / "synthetic_app"
    package.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text(
        """
[project]
name = "synthetic-project-doctor-benchmark"
version = "0.1.0"
dependencies = ["requests>=2"]
""",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    for index in range(files):
        (package / f"module_{index}.py").write_text(
            _synthetic_module(index),
            encoding="utf-8",
        )


def _synthetic_module(index: int) -> str:
    return f"""\
import json
from pathlib import Path
import requests as http

VALUE = json.dumps({{"index": {index}}})


def load_path(raw: str) -> Path:
    return Path(raw)


def fetch(url: str):
    return http.get(url, timeout=5)
"""


if __name__ == "__main__":
    raise SystemExit(main())
