from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

_NORMALIZE_RE = re.compile(r"[-_.]+")
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
_VERSION_SPLIT_RE = re.compile(r"\s*(?:===|==|~=|!=|<=|>=|<|>|@)\s*")

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}

try:
    STDLIB_MODULES = set(sys.stdlib_module_names)
except AttributeError:  # pragma: no cover - project_doctor requires 3.11+, but this keeps the helper defensive.
    STDLIB_MODULES = set(sys.builtin_module_names) | {
        "abc", "argparse", "ast", "asyncio", "collections", "concurrent", "contextlib",
        "csv", "dataclasses", "datetime", "decimal", "email", "functools", "hashlib",
        "html", "http", "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "multiprocessing", "os", "pathlib", "pickle", "re", "shutil", "socket",
        "sqlite3", "statistics", "string", "subprocess", "sys", "tempfile", "textwrap",
        "threading", "time", "tomllib", "traceback", "typing", "unittest", "urllib", "uuid",
        "xml", "zipfile",
    }


def canonicalize_name(name: str) -> str:
    """A small PEP-503-ish package-name normalizer without external dependencies."""
    return _NORMALIZE_RE.sub("-", name).lower().strip("-")


def import_name_guess(distribution_name: str) -> str:
    return canonicalize_name(distribution_name).replace("-", "_")


def top_import_name(module: str) -> str:
    return module.split(".", 1)[0]


def parse_requirement_name(raw: str) -> str | None:
    """Extract a distribution name from a common requirement string.

    This intentionally avoids depending on packaging. It handles the common MVP cases:
    requests>=2, pandas[plot]; python_version>'3.10', flask @ https://..., etc.
    """
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith(("-", "--")):
        return None
    line = line.split(";", 1)[0].strip()
    line = _VERSION_SPLIT_RE.split(line, 1)[0].strip()
    if "[" in line:
        line = line.split("[", 1)[0].strip()
    match = _REQ_NAME_RE.match(line)
    if not match:
        return None
    return match.group(1)


def iter_python_files(root: Path, max_files: int, extra_excludes: Iterable[str] = ()) -> tuple[list[Path], list[str]]:
    excludes = DEFAULT_EXCLUDE_DIRS | set(extra_excludes)
    files: list[Path] = []
    warnings: list[str] = []

    for current_root, dirs, filenames in os.walk(root):
        current = Path(current_root)
        dirs[:] = [d for d in dirs if d not in excludes and not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = current / filename
            files.append(path)
            if len(files) >= max_files:
                warnings.append(f"Stopped after scanning {max_files} Python files. Use --max-files to raise the limit.")
                return files, warnings
    return files, warnings


def is_stdlib_module(name: str) -> bool:
    return top_import_name(name) in STDLIB_MODULES
