from __future__ import annotations

from dataclasses import dataclass, field

from .dependencies import InstalledPackageIndex, estimate_distribution_size
from .models import PackageSize
from .utils import canonicalize_name


@dataclass
class AnalysisContext:
    """Reusable metadata cache for one or more project analyses."""

    installed_packages: InstalledPackageIndex = field(default_factory=InstalledPackageIndex.from_environment)
    _package_size_cache: dict[str, PackageSize] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> AnalysisContext:
        return cls(installed_packages=InstalledPackageIndex.from_environment())

    def package_size(self, distribution_name: str) -> PackageSize:
        key = canonicalize_name(distribution_name)
        if key not in self._package_size_cache:
            self._package_size_cache[key] = estimate_distribution_size(distribution_name)
        return self._package_size_cache[key]
