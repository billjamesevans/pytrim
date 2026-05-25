from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - package requires py3.11+
    tomllib = None  # type: ignore[assignment]

from .dependencies import load_declared_dependencies
from .models import UvLockSummary
from .utils import canonicalize_name


@dataclass(frozen=True)
class UvLockedPackage:
    name: str
    version: str | None


def load_uv_packages(lock_path: Path) -> tuple[list[UvLockedPackage], list[str]]:
    warnings: list[str] = []
    if tomllib is None:
        return [], ["uv.lock exists, but tomllib is unavailable."]
    if not lock_path.exists():
        return [], [f"uv lock file not found: {lock_path}"]

    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [], [f"Could not parse uv.lock: {exc}"]

    packages: list[UvLockedPackage] = []
    raw_packages = data.get("package") or []
    if not isinstance(raw_packages, list):
        return [], ["Could not parse uv.lock: expected [[package]] entries."]

    for item in raw_packages:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        version = item.get("version")
        if isinstance(name, str):
            packages.append(UvLockedPackage(name=name, version=str(version) if version is not None else None))

    return sorted(packages, key=lambda item: canonicalize_name(item.name)), warnings


def sync_check_uv(lock_path: Path) -> UvLockSummary:
    lock_path = lock_path.expanduser().resolve()
    project_root = lock_path.parent
    packages, warnings = load_uv_packages(lock_path)
    if warnings:
        return UvLockSummary(
            lock_path=str(lock_path),
            status="error",
            package_count=len(packages),
            reason=" ".join(warnings),
        )

    declared_dependencies, dep_warnings = load_declared_dependencies(project_root)
    if dep_warnings:
        reason = " ".join(dep_warnings)
    else:
        reason = None

    locked_names = {canonicalize_name(package.name) for package in packages}
    direct_names = {dep.normalized_name: dep.name for dep in declared_dependencies}
    missing = tuple(sorted(direct_names[name] for name in set(direct_names) - locked_names))
    locked_direct = tuple(sorted(direct_names[name] for name in set(direct_names) & locked_names))

    return UvLockSummary(
        lock_path=str(lock_path),
        status="ok" if not missing and reason is None else "out-of-sync",
        package_count=len(packages),
        locked_direct_dependencies=locked_direct,
        missing_direct_dependencies=missing,
        reason=reason,
    )


def locked_package_version(lock_path: Path, package_name: str) -> str | None:
    packages, warnings = load_uv_packages(lock_path.expanduser().resolve())
    if warnings:
        return None
    wanted = canonicalize_name(package_name)
    for package in packages:
        if canonicalize_name(package.name) == wanted:
            return package.version
    return None


def uv_summary_to_dict(summary: UvLockSummary) -> dict[str, Any]:
    return {
        "lock_path": summary.lock_path,
        "status": summary.status,
        "package_count": summary.package_count,
        "locked_direct_dependencies": list(summary.locked_direct_dependencies),
        "missing_direct_dependencies": list(summary.missing_direct_dependencies),
        "reason": summary.reason,
    }
