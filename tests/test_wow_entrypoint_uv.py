from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from project_doctor.analyze import analyze_project
from project_doctor.cli import main
from project_doctor.entrypoint import measure_entrypoint_startup
from project_doctor.models import (
    AnalysisReport,
    DependencyUsage,
    EntrypointTiming,
    ImportTiming,
    LazyImportCandidate,
    PackageSize,
)
from project_doctor.report import render_wow_report
from project_doctor.uv import sync_check_uv


def test_wow_report_surfaces_shareable_quick_wins() -> None:
    report = AnalysisReport(
        project_path="/repo",
        python_files_scanned=12,
        third_party_imports=["pandas", "requests"],
        dependency_usage=[
            DependencyUsage(
                dependency="openpyxl",
                source="pyproject.toml:[project.dependencies]",
                status="unused",
                import_names=("openpyxl",),
                reason="No matching static import was found.",
                confidence="medium",
            )
        ],
        undeclared_imports=["requests"],
        lazy_import_candidates=[
            LazyImportCandidate(
                file="reports.py",
                line=3,
                module="pandas",
                alias="pd",
                reason="Imported at module load but only used inside deferred code.",
                confidence="high",
            )
        ],
        import_timings=[
            ImportTiming(module="pandas", self_ms=10.0, cumulative_ms=684.0, status="ok"),
            ImportTiming(module="matplotlib", self_ms=8.0, cumulative_ms=412.0, status="ok"),
        ],
        package_sizes=[
            PackageSize(distribution="torch", size_mb=742.0, status="ok"),
            PackageSize(distribution="pandas", size_mb=78.0, status="ok"),
        ],
        entrypoint=EntrypointTiming(
            command="python app.py",
            status="ok",
            elapsed_ms=1800.0,
            returncode=0,
            import_timings=[
                ImportTiming(module="pandas", self_ms=10.0, cumulative_ms=684.0, status="ok"),
            ],
        ),
    )

    rendered = render_wow_report(report)

    assert "# Project Doctor Report" in rendered
    assert "Entrypoint startup: 1.8s" in rendered
    assert "- pandas: 684ms" in rendered
    assert "- openpyxl" in rendered
    assert "- requests imported but not declared" in rendered
    assert "- torch: 742MB" in rendered
    assert "1. Move `pandas` import at `reports.py:3` inside the deferred function that uses it." in rendered
    assert "2. Remove `openpyxl` if it is no longer used by active code paths." in rendered
    assert "3. Add `requests` to `pyproject.toml`." in rendered


def test_entrypoint_startup_measures_python_command(tmp_path: Path) -> None:
    script = tmp_path / "app.py"
    script.write_text("import json\nprint(json.dumps({'ok': True}))\n", encoding="utf-8")

    result = measure_entrypoint_startup(
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.status == "ok"
    assert result.returncode == 0
    assert result.elapsed_ms is not None
    assert any(item.module == "json" for item in result.import_timings)


def test_entrypoint_startup_accepts_python_alias(tmp_path: Path) -> None:
    script = tmp_path / "app.py"
    script.write_text("print('ready')\n", encoding="utf-8")

    result = measure_entrypoint_startup(
        f"python {shlex.quote(str(script))}",
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.status == "ok"
    assert result.returncode == 0


def test_analyze_project_attaches_entrypoint_result(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = []
""",
        encoding="utf-8",
    )
    script = tmp_path / "app.py"
    script.write_text("print('ready')\n", encoding="utf-8")

    report = analyze_project(
        tmp_path,
        entrypoint=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
        entrypoint_timeout=5,
    )

    assert report.entrypoint is not None
    assert report.entrypoint.status == "ok"
    assert report.entrypoint.command.endswith("app.py")


def test_cli_analyze_defaults_to_wow_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["definitely-not-installed-project_doctor-test>=1"]
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("", encoding="utf-8")

    status = main(["analyze", str(tmp_path), "--no-import-time"])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out.startswith("# Project Doctor Report")
    assert "Suggested quick wins" in captured.out


def test_doctor_command_is_analyze_alias(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = []
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    status = main(["doctor", str(tmp_path), "--no-import-time"])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out.startswith("# Project Doctor Report")
    assert "Python files scanned: 1" in captured.out


def test_readme_has_ci_copy_paste_and_badge() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "![Project Doctor](https://img.shields.io/badge/project--doctor-passing-brightgreen)" in readme
    assert "project-doctor check . --max-unused 0 --max-undeclared 0 --max-package-mb 100" in readme
    assert "name: project-doctor" in readme
    assert "actions/checkout@v4" in readme


def test_readme_has_sample_report_tagline_and_install_positioning() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "A local-first health checker for Python imports, dependencies, startup time, and package bloat." in readme
    assert "## Example report" in readme
    assert "Startup time: 1.42s" in readme
    assert "Potential avoidable import cost: 630ms" in readme
    assert "Top startup contributors:" in readme
    assert "project-doctor doctor" in readme
    assert "uv tool install project-doctor" in readme
    assert "1. Deeper entrypoint startup benchmarks" in readme


def test_uv_sync_check_reports_missing_direct_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2", "rich>=13"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "requests"
version = "2.32.0"
""",
        encoding="utf-8",
    )

    result = sync_check_uv(tmp_path / "uv.lock")

    assert result.status == "out-of-sync"
    assert result.locked_direct_dependencies == ("requests",)
    assert result.missing_direct_dependencies == ("rich",)


def test_sync_check_cli_outputs_uv_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "requests"
version = "2.32.0"
""",
        encoding="utf-8",
    )

    status = main(["sync-check", str(tmp_path / "uv.lock")])

    captured = capsys.readouterr()
    assert status == 0
    assert "Project Doctor uv sync check passed" in captured.out
    assert "Locked direct dependencies: 1" in captured.out


def test_explain_package_cli_reports_declared_and_locked_package(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "requests"
version = "2.32.0"
""",
        encoding="utf-8",
    )

    status = main(["explain-package", "requests", str(tmp_path), "--uv"])

    captured = capsys.readouterr()
    assert status == 0
    assert "Project Doctor package explanation: requests" in captured.out
    assert "Declared: yes" in captured.out
    assert "uv.lock: locked 2.32.0" in captured.out
