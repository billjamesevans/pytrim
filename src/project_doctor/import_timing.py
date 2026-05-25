from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .models import ImportTiming

_IMPORTTIME_RE = re.compile(r"^import time:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*$")
_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IMPORT_MODULE_CODE = "import importlib, sys; importlib.import_module(sys.argv[1])"


def parse_importtime_output(stderr: str) -> list[ImportTiming]:
    rows: dict[str, tuple[int, int]] = {}
    for line in stderr.splitlines():
        match = _IMPORTTIME_RE.match(line.strip())
        if not match:
            continue
        self_us = int(match.group(1))
        cumulative_us = int(match.group(2))
        imported_name = match.group(3).strip()
        previous = rows.get(imported_name)
        if previous is None or cumulative_us > previous[1]:
            rows[imported_name] = (self_us, cumulative_us)

    timings = [
        ImportTiming(
            module=module,
            self_ms=round(self_us / 1000, 3),
            cumulative_ms=round(cumulative_us / 1000, 3),
            status="ok",
        )
        for module, (self_us, cumulative_us) in rows.items()
    ]
    return sorted(
        timings,
        key=lambda item: (-1 if item.cumulative_ms is None else -item.cumulative_ms, item.module),
    )


def measure_import_time(module: str, timeout_seconds: float, cwd: Path | None = None) -> ImportTiming:
    """Measure a single module import in a subprocess using Python's -X importtime.

    The target module is imported in a child process. This avoids polluting the current
    analyzer process, but the target module's import-time side effects can still occur
    in that child process. Project Doctor only does this when import timing is explicitly enabled.
    """
    if not _MODULE_NAME_RE.fullmatch(module):
        return ImportTiming(
            module=module,
            self_ms=None,
            cumulative_ms=None,
            status="error",
            reason="Invalid module name.",
        )

    if cwd is None:
        with tempfile.TemporaryDirectory(prefix="project_doctor-importtime-") as temp_dir:
            return _measure_import_time(module, timeout_seconds=timeout_seconds, cwd=Path(temp_dir))

    return _measure_import_time(module, timeout_seconds=timeout_seconds, cwd=cwd)


def _measure_import_time(module: str, timeout_seconds: float, cwd: Path) -> ImportTiming:
    cmd = [sys.executable, "-X", "importtime", "-c", _IMPORT_MODULE_CODE, module]
    try:
        # Safe subprocess use: shell=False, fixed code string, validated module passed as argv.
        completed = subprocess.run(  # nosec B603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ImportTiming(
            module=module,
            self_ms=None,
            cumulative_ms=None,
            status="timeout",
            reason=f"Timed out after {timeout_seconds:g}s.",
        )
    except Exception as exc:  # noqa: BLE001
        return ImportTiming(
            module=module,
            self_ms=None,
            cumulative_ms=None,
            status="error",
            reason=str(exc),
        )

    parsed_timings = parse_importtime_output(completed.stderr)
    target = next((item for item in parsed_timings if item.module == module), None)

    if completed.returncode != 0:
        reason = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "Import failed."
        return ImportTiming(
            module=module,
            self_ms=None,
            cumulative_ms=None,
            status="failed",
            reason=reason,
        )

    if target is None:
        # Some imports alias themselves or emit rows with leading package names. Use the
        # largest cumulative row that starts with the requested top-level module.
        related = [
            item
            for item in parsed_timings
            if item.module == module or item.module.startswith(module + ".")
        ]
        if related:
            target = max(related, key=lambda item: -1 if item.cumulative_ms is None else item.cumulative_ms)

    if target is None:
        return ImportTiming(
            module=module,
            self_ms=None,
            cumulative_ms=None,
            status="unknown",
            reason="Could not parse import timing output.",
        )

    return ImportTiming(
        module=module,
        self_ms=target.self_ms,
        cumulative_ms=target.cumulative_ms,
        status="ok",
    )


def measure_import_times(
    modules: Iterable[str],
    limit: int,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> list[ImportTiming]:
    timings: list[ImportTiming] = []
    for module in sorted(set(modules))[:limit]:
        timings.append(measure_import_time(module, timeout_seconds=timeout_seconds, cwd=cwd))
    return sorted(
        timings,
        key=lambda item: (-1 if item.cumulative_ms is None else -item.cumulative_ms, item.module),
    )
