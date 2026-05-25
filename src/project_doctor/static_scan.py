from __future__ import annotations

import ast
import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .models import ImportRecord, LazyImportCandidate, PythonFileScan, relpath
from .utils import top_import_name

_AUTO_PARALLEL_FILE_THRESHOLD = 64


ScanJobs = int | str | None


@dataclass
class _ScanState:
    root: Path
    file: Path
    imports: list[ImportRecord] = field(default_factory=list)
    import_time_uses: set[str] = field(default_factory=set)
    deferred_uses: set[str] = field(default_factory=set)


class _ModuleImportCollector(ast.NodeVisitor):
    """Collect imports and usage in a way that separates import-time from deferred usage.

    Import-time usage is code evaluated while the module imports: module code, class bodies,
    decorators, annotations, and default arguments. Deferred usage is function/method body code.
    """

    def __init__(self, state: _ScanState) -> None:
        self.state = state
        self.function_depth = 0
        self.class_depth = 0

    @property
    def in_deferred_code(self) -> bool:
        return self.function_depth > 0

    @property
    def in_module_level_import_site(self) -> bool:
        # Class bodies execute during import, but imports inside classes are not good lazy-import candidates.
        return self.function_depth == 0 and self.class_depth == 0

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            top = top_import_name(alias.name)
            bound_name = alias.asname or top
            self.state.imports.append(
                ImportRecord(
                    file=relpath(self.state.file, self.state.root),
                    line=node.lineno,
                    module=top,
                    imported=alias.name,
                    alias=bound_name,
                    is_from_import=False,
                    is_module_level=self.in_module_level_import_site,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            return
        if not node.module:
            return
        top = top_import_name(node.module)
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            self.state.imports.append(
                ImportRecord(
                    file=relpath(self.state.file, self.state.root),
                    line=node.lineno,
                    module=top,
                    imported=f"{node.module}.{alias.name}",
                    alias=bound_name,
                    is_from_import=True,
                    is_module_level=self.in_module_level_import_site,
                )
            )

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            if self.in_deferred_code:
                self.state.deferred_uses.add(node.id)
            else:
                self.state.import_time_uses.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function_signature(node)
        self.function_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function_signature(node)
        self.function_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self.function_depth -= 1

    def _visit_function_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults, and annotations are evaluated during import.
        for dec in node.decorator_list:
            self.visit(dec)
        self._visit_arguments_import_time(node.args)
        if node.returns:
            self.visit(node.returns)

    def _visit_arguments_import_time(self, args: ast.arguments) -> None:
        for item in list(args.defaults) + list(args.kw_defaults):
            if item is not None:
                self.visit(item)
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.annotation:
                self.visit(arg.annotation)
        if args.vararg and args.vararg.annotation:
            self.visit(args.vararg.annotation)
        if args.kwarg and args.kwarg.annotation:
            self.visit(args.kwarg.annotation)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        self.class_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self.class_depth -= 1

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        # Lambda body executes later.
        self._visit_arguments_import_time(node.args)
        self.function_depth += 1
        try:
            self.visit(node.body)
        finally:
            self.function_depth -= 1


def scan_python_file(path: Path, root: Path) -> PythonFileScan:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = path.read_text(encoding="latin-1")
    except Exception as exc:  # noqa: BLE001
        return PythonFileScan(
            file=relpath(path, root),
            imports=(),
            import_time_uses=(),
            deferred_uses=(),
            syntax_error=f"Could not read file: {exc}",
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return PythonFileScan(
            file=relpath(path, root),
            imports=(),
            import_time_uses=(),
            deferred_uses=(),
            syntax_error=f"SyntaxError line {exc.lineno}: {exc.msg}",
        )

    state = _ScanState(root=root, file=path)
    _ModuleImportCollector(state).visit(tree)
    return PythonFileScan(
        file=relpath(path, root),
        imports=tuple(state.imports),
        import_time_uses=tuple(sorted(state.import_time_uses)),
        deferred_uses=tuple(sorted(state.deferred_uses)),
    )


def resolve_scan_jobs(jobs: ScanJobs, file_count: int) -> int:
    if file_count <= 1:
        return 1
    if jobs is None:
        return 1

    if isinstance(jobs, str):
        if jobs == "auto":
            if file_count < _AUTO_PARALLEL_FILE_THRESHOLD:
                return 1
            return max(1, min(file_count, os.cpu_count() or 1))
        try:
            parsed_jobs = int(jobs)
        except ValueError as exc:
            raise ValueError("jobs must be a positive integer or 'auto'.") from exc
        jobs = parsed_jobs

    if jobs < 1:
        raise ValueError("jobs must be a positive integer or 'auto'.")
    return min(jobs, file_count)


def iter_scan_python_files(paths: Iterable[Path], root: Path, *, jobs: ScanJobs = 1) -> Iterable[PythonFileScan]:
    path_list = list(paths)
    worker_count = resolve_scan_jobs(jobs, len(path_list))
    if worker_count == 1:
        for path in path_list:
            yield scan_python_file(path, root)
        return

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        yield from executor.map(lambda path: scan_python_file(path, root), path_list)


def scan_python_files(paths: Iterable[Path], root: Path, *, jobs: ScanJobs = 1) -> list[PythonFileScan]:
    return list(iter_scan_python_files(paths, root, jobs=jobs))


def find_lazy_import_candidates(
    scans: Iterable[PythonFileScan],
    heavy_modules: set[str] | None = None,
) -> list[LazyImportCandidate]:
    return sorted(
        iter_lazy_import_candidates(scans, heavy_modules=heavy_modules),
        key=lambda item: (item.file, item.line, item.module, item.alias),
    )


def iter_lazy_import_candidates(
    scans: Iterable[PythonFileScan],
    heavy_modules: set[str] | None = None,
) -> Iterable[LazyImportCandidate]:
    heavy_modules = heavy_modules or set()

    for scan in scans:
        import_time_uses = set(scan.import_time_uses)
        deferred_uses = set(scan.deferred_uses)
        for record in scan.imports:
            if not record.is_module_level:
                continue
            if record.alias in import_time_uses:
                continue
            if record.alias not in deferred_uses:
                continue

            reason = "Imported at module load but only used inside deferred function/method code."
            confidence = "medium"
            if record.module in heavy_modules:
                reason += " The module also appears costly by import time or installed size."
                confidence = "high"
            yield LazyImportCandidate(
                file=record.file,
                line=record.line,
                module=record.module,
                alias=record.alias,
                reason=reason,
                confidence=confidence,
            )


def infer_local_import_roots(root: Path, python_files: Iterable[Path]) -> set[str]:
    """Infer project-local top-level import names from files and packages."""
    roots: set[str] = set()
    root = root.resolve()

    search_bases = [root]
    src = root / "src"
    if src.exists():
        search_bases.append(src)

    for base in search_bases:
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_file() and child.suffix == ".py" and child.name != "setup.py":
                roots.add(child.stem)
            elif child.is_dir() and (child / "__init__.py").exists():
                roots.add(child.name)

    # Fallback: file stems at project root can be imported by scripts.
    for path in python_files:
        try:
            rel = path.resolve().relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) == 1 and path.suffix == ".py":
            roots.add(path.stem)

    return roots
