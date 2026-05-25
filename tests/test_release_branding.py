from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def test_distribution_metadata_uses_project_doctor_brand() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["name"] == "project-doctor"
    assert data["project"]["version"] == "0.6.0"
    assert data["project"]["scripts"] == {"project-doctor": "project_doctor.cli:main"}
    assert data["tool"]["setuptools"]["package-data"] == {"project_doctor": ["py.typed"]}


def test_public_import_package_is_project_doctor() -> None:
    from project_doctor import __version__, analyze_project
    from project_doctor.analyze import analyze_project as direct_analyze_project

    assert __version__ == "0.6.0"
    assert analyze_project is direct_analyze_project


def test_project_doctor_module_entrypoint_runs_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "project_doctor", "--version"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "project-doctor 0.6.0"


def test_readme_uses_project_doctor_install_and_cli_names() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert readme.startswith("# Project Doctor")
    assert "pip install project-doctor" in readme
    assert "uv tool install project-doctor" in readme
    assert "project-doctor doctor" in readme
    assert "from project_doctor import AnalysisContext, analyze_project" in readme
    assert "pytrim" not in readme.lower()


def test_release_docs_cover_pypi_publish_path() -> None:
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = Path("docs/PYPI_RELEASE.md").read_text(encoding="utf-8")

    assert "## 0.6.0" in changelog
    assert "Project Doctor" in changelog
    assert "python -m build" in release_notes
    assert "python -m twine check dist/*" in release_notes
    assert "python -m twine upload dist/*" in release_notes
    assert "project-doctor" in release_notes


def test_pypi_publish_workflow_uses_trusted_publishing() -> None:
    workflow = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "python -m build" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "environment: pypi" in workflow
