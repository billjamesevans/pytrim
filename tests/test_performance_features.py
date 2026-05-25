from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from project_doctor.analyze import analyze_project
from project_doctor.cli import main
from project_doctor.models import AnalysisReport, PackageSize
from project_doctor.static_scan import scan_python_files


def test_package_size_collection_is_opt_in(tmp_path: Path) -> None:
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

    report = analyze_project(tmp_path, run_import_timing=False)

    assert report.package_sizes == []
    assert "Package size checks disabled by default. Use --package-sizes to enable installed size checks." in (
        report.warnings
    )


def test_package_size_collection_can_be_enabled(tmp_path: Path) -> None:
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

    report = analyze_project(tmp_path, run_import_timing=False, collect_package_sizes=True)

    assert [item.distribution for item in report.package_sizes] == ["definitely-not-installed-project_doctor-test"]
    assert report.package_sizes[0].status == "unavailable"


def test_check_package_threshold_enables_size_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_analyze_project(*args: object, **kwargs: object) -> AnalysisReport:
        captured.update(kwargs)
        return AnalysisReport(project_path=str(tmp_path), python_files_scanned=0)

    monkeypatch.setattr("project_doctor.cli.analyze_project", fake_analyze_project)

    status = main(["check", str(tmp_path), "--max-package-mb", "10"])

    assert status == 0
    assert captured["collect_package_sizes"] is True


def test_installed_package_index_keeps_reverse_distribution_lookup() -> None:
    from project_doctor.dependencies import InstalledPackageIndex, distribution_import_names

    index = InstalledPackageIndex.from_import_to_distributions(
        {
            "yaml": ("PyYAML",),
            "sklearn": ("scikit-learn",),
            "pil": ("Pillow", "legacy-pil"),
        }
    )

    assert index.import_names_for_distribution("pyyaml") == ("yaml",)
    assert index.import_names_for_distribution("scikit_learn") == ("sklearn",)
    assert index.import_names_for_distribution("pillow") == ("pil",)
    assert distribution_import_names("PyYAML", index) == ("yaml",)


def test_analysis_context_caches_package_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    from project_doctor.context import AnalysisContext
    from project_doctor.dependencies import InstalledPackageIndex

    calls: list[str] = []

    def fake_estimate(distribution_name: str) -> PackageSize:
        calls.append(distribution_name)
        return PackageSize(distribution=distribution_name, size_mb=1.25, status="ok")

    monkeypatch.setattr("project_doctor.context.estimate_distribution_size", fake_estimate)
    context = AnalysisContext(
        installed_packages=InstalledPackageIndex.from_import_to_distributions({}),
    )

    assert context.package_size("Demo-Package") == PackageSize(
        distribution="Demo-Package",
        size_mb=1.25,
        status="ok",
    )
    assert context.package_size("Demo-Package") == PackageSize(
        distribution="Demo-Package",
        size_mb=1.25,
        status="ok",
    )
    assert calls == ["Demo-Package"]


def test_parallel_static_scan_matches_serial(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index in range(12):
        path = tmp_path / f"module_{index}.py"
        path.write_text(f"import json\nVALUE = json.dumps({index})\n", encoding="utf-8")
        paths.append(path)

    serial = scan_python_files(paths, tmp_path, jobs=1)
    parallel = scan_python_files(paths, tmp_path, jobs=2)

    assert parallel == serial


def test_auto_jobs_static_scan_matches_serial_for_larger_projects(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index in range(80):
        path = tmp_path / f"module_{index}.py"
        path.write_text(f"from pathlib import Path\nVALUE = Path({index!r})\n", encoding="utf-8")
        paths.append(path)

    serial = scan_python_files(paths, tmp_path, jobs=1)
    automatic = scan_python_files(paths, tmp_path, jobs="auto")

    assert automatic == serial


def test_benchmark_script_smoke() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "benchmark.py"),
            "--files",
            "5",
            "--runs",
            "1",
            "--jobs",
            "1",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["files"] >= 5
    assert payload["requested_files"] == 5
    assert payload["runs"] == 1
    assert payload["jobs"] == "1"
    assert payload["median_seconds"] >= 0
