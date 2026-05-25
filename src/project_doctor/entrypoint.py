from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

from .import_timing import parse_importtime_output
from .models import EntrypointTiming


def measure_entrypoint_startup(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float,
) -> EntrypointTiming:
    args = shlex.split(command)
    if not args:
        return EntrypointTiming(
            command=command,
            status="error",
            elapsed_ms=None,
            returncode=None,
            reason="Entrypoint command is empty.",
        )
    if args[0] in {"python", "python3"}:
        args[0] = sys.executable

    env = os.environ.copy()
    env["PYTHONPROFILEIMPORTTIME"] = "1"
    start = time.perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
            args,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        stderr = _timeout_text(exc.stderr)
        return EntrypointTiming(
            command=command,
            status="timeout",
            elapsed_ms=elapsed_ms,
            returncode=None,
            import_timings=parse_importtime_output(stderr),
            reason=f"Timed out after {timeout_seconds:g}s.",
        )
    except Exception as exc:  # noqa: BLE001
        return EntrypointTiming(
            command=command,
            status="error",
            elapsed_ms=None,
            returncode=None,
            reason=str(exc),
        )

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    status = "ok" if completed.returncode == 0 else "failed"
    reason = None
    if completed.returncode != 0:
        reason = f"Command exited with status {completed.returncode}."
    return EntrypointTiming(
        command=command,
        status=status,
        elapsed_ms=elapsed_ms,
        returncode=completed.returncode,
        import_timings=parse_importtime_output(completed.stderr),
        reason=reason,
    )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
