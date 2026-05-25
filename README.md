# Project Doctor

[![CI](https://github.com/billjamesevans/project-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/billjamesevans/project-doctor/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Project Doctor](https://img.shields.io/badge/project--doctor-passing-brightgreen)

A local-first health checker for Python imports, dependencies, startup time, and package bloat.

Project Doctor is a zero-dependency analyzer for finding project optimization work that usually hides in plain sight:

- entrypoint startup drag
- slow third-party imports
- likely unused dependencies
- possible undeclared imports
- opt-in large installed package checks
- top-level imports that look safe to move into deferred code paths
- CI thresholds for dependency and import hygiene
- uv.lock sync status and package explanations

Project Doctor never edits your code. It produces reviewable reports and CI-friendly checks so teams can make deliberate changes.

## Security and privacy

Project Doctor is local-first and safe by default. Static scans parse source files with Python's `ast` module and do not import your project code. Optional import timing must be enabled with `--import-time`; it imports third-party modules in child processes, so leave it off when reviewing sensitive projects or code with import-time side effects. Installed package size checks are also opt-in with `--package-sizes` because they walk installed distribution metadata.

## Install

Current reliable install path:

From this folder:

```bash
python3 -m pip install -e .
```

Then run:

```bash
project-doctor analyze /path/to/your/project
```

Or without installing:

```bash
PYTHONPATH=src python3 -m project_doctor analyze /path/to/your/project
```

After the PyPI release is live:

```bash
pip install project-doctor
uv tool install project-doctor
```

## Quick examples

```bash
project-doctor doctor
project-doctor analyze examples/sample_project
project-doctor analyze examples/sample_project --json -o project-doctor-report.json
project-doctor analyze examples/sample_project --jobs auto --package-sizes
project-doctor analyze examples/sample_project --entrypoint "python app.py"
project-doctor analyze examples/sample_project --uv
project-doctor check examples/sample_project --max-unused 0
project-doctor check examples/sample_project --import-time --json --max-import-ms 150
project-doctor sync-check examples/sample_project/uv.lock
project-doctor explain-package pandas examples/sample_project --uv
```

`analyze` writes a shareable "wow" report by default. Use `--report detailed` for the longer audit report. `check` prints a compact status report and exits nonzero when a configured threshold is exceeded.

## Example report

```text
Project Doctor Report

Startup time: 1.42s
Potential avoidable import cost: 630ms

Top startup contributors:
- pandas: 312ms
- boto3: 205ms
- matplotlib: 113ms

Likely unused dependencies:
- openpyxl
- beautifulsoup4
- python-dotenv

Possible undeclared imports:
- requests imported but not declared

Largest installed packages:
- torch: 742MB
- pandas: 78MB
- botocore: 71MB

Suggested quick wins:
1. Move `pandas` import at `reports.py:3` inside the deferred function that uses it.
2. Remove `openpyxl` if it is no longer used by active code paths.
3. Add `requests` to `pyproject.toml`.
```

## Python API

```python
from project_doctor import AnalysisContext, analyze_project

context = AnalysisContext.from_environment()
report = analyze_project(
    "examples/sample_project",
    context=context,
    jobs="auto",
    run_import_timing=False,
    collect_package_sizes=False,
    entrypoint="python app.py",
)
print(report.unused_dependencies)
```

The returned `AnalysisReport` and nested report objects are dataclasses and can be converted to dictionaries with `report.to_dict()`. Reuse `AnalysisContext` when analyzing multiple projects in one process; it keeps installed package metadata and package size estimates cached.

## What Project Doctor checks

### Static import scan

Project Doctor parses Python source with `ast`, so it can read imports without importing your project code. Syntax errors are reported as warnings instead of aborting the full scan.

### Dependency usage

Project Doctor reads dependencies from:

- `pyproject.toml` `[project.dependencies]`
- `pyproject.toml` `[project.optional-dependencies]`
- `pyproject.toml` `[dependency-groups]`, including `{ include-group = "..." }`
- common Poetry dependency sections
- `requirements*.txt`, including nested `-r` and `--requirement` includes

It compares declared dependencies against static imports. Results marked `unused` should be treated as a review queue, not an automatic delete list.

### Import timings

When `--import-time` is enabled, Project Doctor runs a subprocess like this for each likely third-party top-level import:

```bash
python -X importtime -c "import pandas"
```

This keeps imports out of the analyzer process, but the imported library can still run import-time side effects in the child process. Leave import timing disabled when you want a purely static scan.

### Entrypoint startup timing

Entrypoint mode measures the command users actually wait on:

```bash
project-doctor analyze . --entrypoint "python app.py"
project-doctor analyze . --entrypoint "uvicorn app:app"
project-doctor analyze . --entrypoint "python -m my_cli"
```

Project Doctor runs the command with Python import profiling enabled and `shell=False`, then folds the parsed startup import costs into the report. Server-style commands may time out by design; Project Doctor still reports import data captured before the timeout.

### Lazy-import candidates

Project Doctor looks for imports that are defined at module load but only used inside deferred function or method bodies.

For example:

```python
import pandas as pd


def make_report(rows):
    return pd.DataFrame(rows)
```

Project Doctor may suggest:

```python
def make_report(rows):
    import pandas as pd
    return pd.DataFrame(rows)
```

That kind of change can reduce startup time for CLIs, Lambdas, Flask/FastAPI apps, and agent scripts when a heavy dependency is only needed on a less-common path.

## CLI

```bash
project-doctor doctor [path] [options]

Runs the default project health report. It is an alias for `project-doctor analyze .` with the same analysis options.
```

```bash
project-doctor analyze [path] [options]

Options:
  --json                         Emit JSON instead of Markdown
  --report wow|detailed           Human report style, default wow
  --output FILE, -o FILE          Write report to a file
  --import-time                   Run subprocess import timing checks
  --no-import-time                Skip subprocess import timing checks, default
  --import-time-limit N           Max third-party modules to time, default 20
  --import-time-timeout SECONDS   Timeout per import, default 10
  --package-sizes                 Collect installed package sizes
  --no-package-sizes              Skip installed package size checks, default
  --jobs N|auto                   Static scan workers, default auto
  --entrypoint COMMAND            Measure startup for a real entrypoint command
  --entrypoint-timeout SECONDS    Timeout for entrypoint measurement, default 10
  --uv                            Include uv.lock status
  --max-files N                   Max Python files to scan, default 5000
  --exclude DIR                   Extra directory name to exclude; repeatable
```

```bash
project-doctor check [path] [options]

Options:
  --json                         Emit machine-readable check results
  --max-unused N                  Max likely unused dependencies, default 0
  --max-undeclared N              Max possible undeclared imports, default 0
  --max-lazy-imports N            Max lazy-import candidates
  --max-import-ms N               Max cumulative import time for any measured module
  --max-package-mb N              Max installed package size; enables package size checks
  --import-time                   Run subprocess import timing checks
  --no-import-time                Skip subprocess import timing checks, default
  --import-time-limit N           Max third-party modules to time, default 20
  --import-time-timeout SECONDS   Timeout per import, default 10
  --package-sizes                 Collect installed package sizes
  --no-package-sizes              Skip installed package size checks, default
  --jobs N|auto                   Static scan workers, default auto
  --entrypoint COMMAND            Measure startup for a real entrypoint command
  --entrypoint-timeout SECONDS    Timeout for entrypoint measurement, default 10
  --uv                            Include uv.lock status
  --max-files N                   Max Python files to scan, default 5000
  --exclude DIR                   Extra directory name to exclude; repeatable
```

```bash
project-doctor sync-check [uv.lock] [options]

Options:
  --json                         Emit machine-readable sync results
```

```bash
project-doctor explain-package PACKAGE [path] [options]

Options:
  --uv                           Include uv.lock status
  --json                         Emit machine-readable package explanation
```

## CI

Add Project Doctor to GitHub Actions as a dependency hygiene gate:

```yaml
- name: Check Python dependency health
  run: project-doctor check . --max-unused 0 --max-undeclared 0 --max-package-mb 100
```

Intended full workflow after first package release and name/package ownership confirmation:

```yaml
name: project-doctor

on: [push, pull_request]

jobs:
  project-doctor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install project-doctor
      - run: project-doctor check . --max-unused 0 --max-undeclared 0
```

For projects that use uv:

```yaml
- name: Check uv lock sync
  run: project-doctor sync-check uv.lock
```

Badge for project docs:

```markdown
![Project Doctor](https://img.shields.io/badge/project--doctor-passing-brightgreen)
```

## uv

Project Doctor understands the common `pyproject.toml` + `uv.lock` workflow:

```bash
project-doctor analyze --uv
project-doctor sync-check uv.lock
project-doctor explain-package pandas --uv
```

`sync-check` verifies that direct dependencies declared in `pyproject.toml` are represented in `uv.lock`. `explain-package` shows whether a package is declared, which import names map to it, whether it is installed locally, and whether uv has locked it.

## Performance

Project Doctor keeps the default path fast:

- installed package metadata is indexed once per analysis context
- package size checks are skipped unless requested or needed by `--max-package-mb`
- static scans stream into aggregate results instead of retaining every file scan object
- `--jobs auto` uses serial scanning for small projects and bounded parallel scanning for larger projects

Run the benchmark helper against a synthetic project:

```bash
PYTHONPATH=src python3 scripts/benchmark.py --files 1000 --runs 5 --jobs auto
```

Or benchmark a real project:

```bash
PYTHONPATH=src python3 scripts/benchmark.py /path/to/project --runs 5 --jobs auto
```

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,security]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src/project_doctor tests
.venv/bin/python -m bandit -r src examples scripts -q
.venv/bin/python -m pip_audit
.venv/bin/python scripts/benchmark.py --files 200 --runs 2
.venv/bin/python -m build
```

## Current limitations

- Static analysis misses dynamic imports and plugin systems.
- Dependency names do not always match import names.
- Optional dependencies may be marked unused if their optional code path is not statically imported.
- Opt-in import timing imports third-party packages in a subprocess, which can still trigger child-process side effects.
- Entrypoint mode runs your command in a subprocess; use it for commands that are safe to execute locally.
- Package size checks are opt-in and only work for dependencies installed in the current environment.

## Roadmap

The next serious versions should add:

1. Deeper entrypoint startup benchmarks with clearer default-path waste attribution.
2. `project-doctor fix --lazy-imports` with AST-safe rewrites and backups.
3. Lockfile awareness for Poetry, PDM, and pip-tools.
4. Docker/image-size analysis.
5. Richer package-name/import-name mapping.
6. Profiler integration for hot-loop acceleration suggestions.
