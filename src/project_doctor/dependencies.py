from __future__ import annotations

import importlib.metadata as metadata
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - package requires py3.11+
    tomllib = None  # type: ignore[assignment]

from .models import DeclaredDependency, DependencyUsage, PackageSize
from .utils import canonicalize_name, import_name_guess, parse_requirement_name


@dataclass(frozen=True)
class InstalledPackageIndex:
    """Lookup table for installed package metadata.

    `importlib.metadata.packages_distributions()` returns import-name -> distribution-name.
    Project Doctor needs both directions, so build the reverse index once per analysis context
    instead of scanning the full mapping for every declared dependency.
    """

    import_to_distributions: dict[str, tuple[str, ...]]
    distribution_to_imports: dict[str, tuple[str, ...]]

    @classmethod
    def from_environment(cls) -> InstalledPackageIndex:
        try:
            mapping = metadata.packages_distributions()
        except Exception:  # pragma: no cover - extremely defensive.
            return cls.from_import_to_distributions({})
        return cls.from_import_to_distributions(mapping)

    @classmethod
    def from_import_to_distributions(
        cls,
        mapping: Mapping[str, Iterable[str]],
    ) -> InstalledPackageIndex:
        import_to_distributions: dict[str, tuple[str, ...]] = {}
        reverse: dict[str, set[str]] = {}
        for import_name, distributions in mapping.items():
            normalized_import = str(import_name)
            distribution_names = tuple(sorted({str(distribution) for distribution in distributions}))
            import_to_distributions[normalized_import] = distribution_names
            for distribution in distribution_names:
                reverse.setdefault(canonicalize_name(distribution), set()).add(normalized_import)

        distribution_to_imports = {
            distribution: tuple(sorted(import_names))
            for distribution, import_names in reverse.items()
        }
        return cls(
            import_to_distributions=import_to_distributions,
            distribution_to_imports=distribution_to_imports,
        )

    def import_names_for_distribution(self, distribution: str) -> tuple[str, ...]:
        names = self.distribution_to_imports.get(canonicalize_name(distribution))
        if names:
            return names
        return (import_name_guess(distribution),)

    def providers_for_import(self, import_name: str) -> tuple[str, ...]:
        return self.import_to_distributions.get(import_name, ())


def load_declared_dependencies(project_root: Path) -> tuple[list[DeclaredDependency], list[str]]:
    """Read dependencies from pyproject.toml and requirements*.txt.

    This is deliberately conservative. The goal is to find likely dependency bloat,
    not to perfectly implement every packaging backend.
    """
    dependencies: dict[tuple[str, str], DeclaredDependency] = {}
    warnings: list[str] = []

    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        if tomllib is None:
            warnings.append("pyproject.toml exists, but tomllib is unavailable.")
        else:
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - report to user, do not crash analysis.
                warnings.append(f"Could not parse pyproject.toml: {exc}")
            else:
                _add_pep621_dependencies(data, dependencies)
                _add_poetry_dependencies(data, dependencies)
                _add_dependency_groups(data, dependencies, warnings)

    for req_file in sorted(project_root.glob("requirements*.txt")):
        _add_requirements_file(req_file, dependencies, warnings, seen=set())

    return sorted(dependencies.values(), key=lambda d: (d.source, d.normalized_name)), warnings


def _remember(deps: dict[tuple[str, str], DeclaredDependency], name: str, raw: str, source: str) -> None:
    normalized = canonicalize_name(name)
    deps[(source, normalized)] = DeclaredDependency(
        name=name,
        normalized_name=normalized,
        source=source,
        raw=raw,
    )


def _add_pep621_dependencies(data: dict[str, Any], deps: dict[tuple[str, str], DeclaredDependency]) -> None:
    project = data.get("project") or {}
    for raw in project.get("dependencies") or []:
        name = parse_requirement_name(str(raw))
        if name:
            _remember(deps, name, str(raw), "pyproject.toml:[project.dependencies]")

    optional = project.get("optional-dependencies") or {}
    if isinstance(optional, dict):
        for group, reqs in optional.items():
            for raw in reqs or []:
                name = parse_requirement_name(str(raw))
                if name:
                    _remember(deps, name, str(raw), f"pyproject.toml:[project.optional-dependencies.{group}]")


def _add_poetry_dependencies(data: dict[str, Any], deps: dict[tuple[str, str], DeclaredDependency]) -> None:
    poetry = ((data.get("tool") or {}).get("poetry") or {})
    for section_name in ("dependencies", "dev-dependencies"):
        section = poetry.get(section_name) or {}
        if not isinstance(section, dict):
            continue
        for name, spec in section.items():
            if canonicalize_name(name) == "python":
                continue
            raw = f"{name} {spec}" if isinstance(spec, str) else f"{name} {spec!r}"
            _remember(deps, str(name), raw, f"pyproject.toml:[tool.poetry.{section_name}]")

    groups = (poetry.get("group") or {})
    if isinstance(groups, dict):
        for group_name, group_data in groups.items():
            section = ((group_data or {}).get("dependencies") or {})
            if not isinstance(section, dict):
                continue
            for name, spec in section.items():
                raw = f"{name} {spec}" if isinstance(spec, str) else f"{name} {spec!r}"
                _remember(deps, str(name), raw, f"pyproject.toml:[tool.poetry.group.{group_name}.dependencies]")


def _add_dependency_groups(
    data: dict[str, Any],
    deps: dict[tuple[str, str], DeclaredDependency],
    warnings: list[str],
) -> None:
    groups = data.get("dependency-groups") or {}
    if not isinstance(groups, dict):
        warnings.append("Ignoring [dependency-groups]: expected a table.")
        return

    def add_group(group_name: str, source_group: str, stack: tuple[str, ...]) -> None:
        if group_name in stack:
            chain = " -> ".join((*stack, group_name))
            warnings.append(f"Ignoring cyclic dependency group include: {chain}.")
            return

        entries = groups.get(group_name)
        if entries is None:
            warnings.append(f"Ignoring missing dependency group included by {source_group!r}: {group_name!r}.")
            return
        if not isinstance(entries, list):
            warnings.append(f"Ignoring dependency group {group_name!r}: expected a list.")
            return

        source = f"pyproject.toml:[dependency-groups.{source_group}]"
        for entry in entries:
            if isinstance(entry, str):
                name = parse_requirement_name(entry)
                if name:
                    _remember(deps, name, entry, source)
                continue

            if isinstance(entry, dict) and set(entry) == {"include-group"}:
                included = entry.get("include-group")
                if isinstance(included, str):
                    add_group(included, source_group, (*stack, group_name))
                else:
                    warnings.append(f"Ignoring invalid include-group in dependency group {group_name!r}.")
                continue

            warnings.append(f"Ignoring unsupported dependency group entry in {group_name!r}: {entry!r}.")

    for group_name in sorted(groups):
        add_group(str(group_name), str(group_name), stack=())


def _add_requirements_file(
    req_file: Path,
    deps: dict[tuple[str, str], DeclaredDependency],
    warnings: list[str],
    seen: set[Path],
) -> None:
    req_file = req_file.resolve()
    if req_file in seen:
        return
    seen.add(req_file)

    try:
        lines = req_file.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Could not read {req_file.name}: {exc}")
        return
    for raw in lines:
        included = _parse_requirement_include(raw)
        if included:
            include_path = (req_file.parent / included).resolve()
            _add_requirements_file(include_path, deps, warnings, seen=seen)
            continue

        name = parse_requirement_name(raw)
        if name:
            _remember(deps, name, raw, req_file.name)


def _parse_requirement_include(raw: str) -> str | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    for prefix in ("-r", "--requirement"):
        if line == prefix:
            return None
        if line.startswith(prefix + " "):
            return line[len(prefix) :].strip().split(maxsplit=1)[0]
        if line.startswith(prefix + "="):
            return line[len(prefix) + 1 :].strip().split(maxsplit=1)[0]
    return None


def installed_import_name_map() -> dict[str, tuple[str, ...]]:
    """Return import-name -> distribution-name map using installed package metadata."""
    return InstalledPackageIndex.from_environment().import_to_distributions


def distribution_import_names(
    distribution: str,
    package_index: InstalledPackageIndex | Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    if isinstance(package_index, InstalledPackageIndex):
        return package_index.import_names_for_distribution(distribution)

    canonical_dist = canonicalize_name(distribution)
    names = [
        import_name
        for import_name, distributions in package_index.items()
        if any(canonicalize_name(dist) == canonical_dist for dist in distributions)
    ]
    if names:
        return tuple(sorted(set(names)))
    return (import_name_guess(distribution),)


def dependency_usage_status(
    declared_dependencies: Iterable[DeclaredDependency],
    all_imports: set[str],
    package_index: InstalledPackageIndex | Mapping[str, tuple[str, ...]],
) -> list[DependencyUsage]:
    usage: list[DependencyUsage] = []
    for dep in declared_dependencies:
        import_names = distribution_import_names(dep.name, package_index)
        used_names = tuple(sorted(set(import_names) & all_imports))
        if used_names:
            usage.append(
                DependencyUsage(
                    dependency=dep.name,
                    source=dep.source,
                    status="used",
                    import_names=used_names,
                    reason="A matching import name was found in the source tree.",
                    confidence="high",
                )
            )
        else:
            usage.append(
                DependencyUsage(
                    dependency=dep.name,
                    source=dep.source,
                    status="unused",
                    import_names=import_names,
                    reason=(
                        "No matching static import was found. This can miss dynamic imports, "
                        "plugins, CLI entry points, and optional code paths."
                    ),
                    confidence="medium",
                )
            )
    return usage


def undeclared_imports(
    third_party_imports: Iterable[str],
    declared_dependencies: Iterable[DeclaredDependency],
    package_index: InstalledPackageIndex | Mapping[str, tuple[str, ...]],
) -> list[str]:
    declared = {dep.normalized_name for dep in declared_dependencies}
    missing: set[str] = set()
    for import_name in third_party_imports:
        if isinstance(package_index, InstalledPackageIndex):
            providers = package_index.providers_for_import(import_name)
        else:
            providers = package_index.get(import_name, ())
        if providers:
            if not any(canonicalize_name(provider) in declared for provider in providers):
                missing.add(import_name)
        else:
            guessed = canonicalize_name(import_name.replace("_", "-"))
            if guessed not in declared:
                missing.add(import_name)
    return sorted(missing)


def estimate_distribution_size(distribution_name: str) -> PackageSize:
    """Estimate installed distribution size from metadata files.

    Returns unavailable when the dependency is not installed in the current environment.
    """
    try:
        dist = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return PackageSize(
            distribution=distribution_name,
            size_mb=None,
            status="unavailable",
            reason="Distribution is not installed in this environment.",
        )
    except Exception as exc:  # noqa: BLE001
        return PackageSize(
            distribution=distribution_name,
            size_mb=None,
            status="error",
            reason=str(exc),
        )

    total = 0
    files = dist.files or []
    for file in files:
        try:
            path = Path(str(dist.locate_file(file)))
        except (OSError, ValueError):
            path = None
        if path is not None and path.is_file():
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return PackageSize(
        distribution=distribution_name,
        size_mb=round(total / (1024 * 1024), 2),
        status="ok",
        reason=None,
    )
