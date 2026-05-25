from pathlib import Path

import pytest

from project_doctor import analyze_project as public_analyze_project
from project_doctor.analyze import analyze_project
from project_doctor.cli import main
from project_doctor.import_timing import measure_import_time


def test_sample_project_static_analysis() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "sample_project"
    report = analyze_project(root, run_import_timing=False)
    assert "pandas" in report.third_party_imports
    assert any(item.module == "pandas" for item in report.lazy_import_candidates)
    assert any(item.dependency == "numpy" and item.status == "unused" for item in report.dependency_usage)


def test_public_api_exports_analyze_project() -> None:
    assert public_analyze_project is analyze_project


def test_dependency_groups_and_includes_are_loaded(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2"]

[dependency-groups]
test = ["pytest>=8", { include-group = "coverage" }]
coverage = ["coverage[toml]>=7"]
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    report = analyze_project(tmp_path, run_import_timing=False)

    by_name = {item.name: item for item in report.declared_dependencies}
    assert {"requests", "pytest", "coverage"} <= set(by_name)
    assert by_name["pytest"].source == "pyproject.toml:[dependency-groups.test]"
    assert by_name["coverage"].source == "pyproject.toml:[dependency-groups.test]"


def test_nested_requirements_files_are_loaded(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("-r requirements-dev.txt\nrequests>=2\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest>=8\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    report = analyze_project(tmp_path, run_import_timing=False)

    by_name = {item.name: item for item in report.declared_dependencies}
    assert {"requests", "pytest"} <= set(by_name)
    assert by_name["pytest"].source == "requirements-dev.txt"


def test_check_command_fails_when_threshold_is_exceeded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2", "numpy>=1"]
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    status = main(["check", str(tmp_path), "--no-import-time", "--max-unused", "0"])

    captured = capsys.readouterr()
    assert status == 1
    assert "Likely unused dependencies: 1 > 0" in captured.out


def test_analysis_does_not_run_import_timing_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("import timing should be opt-in")

    monkeypatch.setattr("project_doctor.analyze.measure_import_times", fail_if_called)

    report = analyze_project(tmp_path)

    assert report.import_timings == []
    assert "Import timing disabled by default. Use --import-time to enable subprocess timing checks." in report.warnings


def test_import_timing_rejects_invalid_module_names() -> None:
    result = measure_import_time("json; raise SystemExit(99)", timeout_seconds=1)

    assert result.status == "error"
    assert result.reason == "Invalid module name."
