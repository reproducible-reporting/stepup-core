# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities for testing with pytest."""

import ast
import asyncio
import functools
import importlib.util
import os
import re
import shutil
import subprocess
import sys

import pytest
from path import Path

from .constants import DIRECTOR_LOG, STEPUP_DIR
from .enums import ReturnCode
from .utils import scan_director_log

__all__ = ("ConventionTests", "remove_hashes", "run_example", "run_plan")


def remove_hashes(graph: dict) -> dict:
    """Remove hashes from a JSON representation of the workflow."""
    for node in graph["nodes"]:
        node.pop("h", None)
    return graph


STDERR_BEGIN = "──────────────────────────────── Standard error ────────────────────────────────"
STDERR_END = "────────────────────────────────────────────────────────────────────────────────"

MAIN_LOG = "main.log"
"""The file in which the trace of `main.sh` is kept, printed with every failing example.

Examples run under `bash -x`, so this log names the command each example died on,
next to whatever that command wrote to stderr.
Without it, a failure that stops `main.sh` early is reported as a missing `current_*` file,
which says nothing about what went wrong.
"""

EXAMPLE_TIMEOUT = 30.0
"""Seconds to wait for an example to finish before failing the test."""


async def run_example(srcdir: Path, tmpdir: Path, overwrite_expected=False):
    """Run an example use case in a temporary directory and check the outputs.

    The script `main.sh` in the example is the entry point for the test case.
    It must have one or more lines of the form
    `stepup ... & # > current_stdout{something}.txt &`,
    where `sb`, the shortcut for `stepup build`, may be used instead of `stepup`.
    The ` & #` in such a line is removed before the example runs,
    so that the reporter output is captured for comparison with the expected output.

    All files in the srcdir starting with `expected_` will be compared to corresponding files
    starting with `current_` in the temporary directory after completion of the example.

    Parameters
    ----------
    srcdir
        The source directory of the example.
    tmpdir
        The temporary directory where the example is executed.
    overwrite_expected
        Update the expected outputs in the source with the outputs from the tmpdir.
    """
    workdir = tmpdir / "example"
    shutil.copytree(srcdir, workdir)
    # Make the shared boilerplate available at `../example.rc` relative to `main.sh`.
    shutil.copy(srcdir.parent / "example.rc", workdir.parent / "example.rc")

    # Rewrite the script to activate the commented-out redirection of the stepup output.
    sed_proc = await asyncio.create_subprocess_shell(
        r"sed -i -e '/^\(stepup\|sb\)/ s/ & #//' main.sh",
        stdin=subprocess.DEVNULL,
        cwd=workdir,
    )
    await sed_proc.wait()
    assert sed_proc.returncode == 0
    # The trace of `main.sh` goes to a file instead of the inherited streams,
    # so that it survives a timeout and is printed as one block, whatever happens.
    with open(workdir / MAIN_LOG, "w") as main_log:
        stepup_proc = await asyncio.create_subprocess_shell(
            "." / Path("main.sh"),
            stdin=subprocess.DEVNULL,
            stdout=main_log,
            stderr=subprocess.STDOUT,
            cwd=workdir,
            env=os.environ | {"PYTHONUNBUFFERED": "yes", "COLUMNS": "80", "STEPUP_DEBUG": "1"},
        )
    try:
        async with asyncio.timeout(EXAMPLE_TIMEOUT):
            await stepup_proc.wait()

        pairs = []

        for path_exp in sorted(srcdir.glob("expected*.*")):
            fn_exp = path_exp.basename()
            path_cur = workdir / ("current" + fn_exp[8:])
            if not path_cur.is_file():
                raise AssertionError(
                    f"{path_cur.basename()} was not created and main.sh exited with "
                    f"{stepup_proc.returncode}. See the {MAIN_LOG} below for the command "
                    f"it died on."
                )
            with open(path_cur) as fh:
                cur = fh.read().rstrip()

            # Print the current output for debugging purposes,
            # before normalizing it for comparison with the expected output.
            print()
            print(f"########## {fn_exp} ##########")
            print()
            print(cur)

            # Normalize output before comparing:
            cur = cur.replace(Path.cwd(), "${PWD}")
            cur = cur.replace(workdir, "${CASE}")
            # - The director's socket path contains a random temporary directory
            cur = re.sub(r"DIRECTOR │ Listening on .*\n", "", cur)
            # - Exact line numbers in exceptions change often and do not matter here
            cur = re.sub(r", line \d+, in ", ", line ---, in ", cur)
            # - Remove new types of traceback output not introduced after Python 3.11,
            #   which is the oldest version we support.
            cur = re.sub(r"^    \.{3}<\d+ lines>\.{3}\n", "", cur, flags=re.MULTILINE)
            cur = re.sub(r"^    \)\n", "", cur, flags=re.MULTILINE)
            cur = re.sub(r"    \~*\^*\n", "", cur, flags=re.MULTILINE)
            # - Remove trailing whitespace
            cur = re.sub(r"[ \t]+?(\n|\Z)", r"\1", cur)
            # - Remove digests: they change often,
            #   so the content of results must be tested explicitly.
            cur = re.sub(r" {10}(.{4})digest = [ 0-9a-f]{71}\n", "", cur)
            # - Strip the body of the standard error page: it is sensitive to OS and Python version
            cur = re.sub(
                STDERR_BEGIN + r".*?" + STDERR_END,
                STDERR_BEGIN + "\n(stripped)\n" + STDERR_END,
                cur,
                flags=re.DOTALL,
            )
            # - Timings are not deterministic
            cur = re.sub(r"DIRECTOR │ Wall .*\n", "", cur)

            # Overwrite the expected output or keep the pair for the comparison below.
            if overwrite_expected:
                path_exp = srcdir / fn_exp
                with open(path_exp, "w") as fh:
                    print(cur, file=fh)
            else:
                with open(path_exp) as fh:
                    exp = fh.read().rstrip()
                pairs.append((path_exp, cur, exp))
    finally:
        for path_log in [workdir / MAIN_LOG, *sorted(workdir.glob(STEPUP_DIR / "*.log"))]:
            print()
            print(f"########## {path_log} ##########")
            print()
            with open(path_log) as fh:
                print(fh.read().rstrip())
        print()
        # `None` means the example was still running when it ran out of time.
        print(f"########## main.sh return code: {stepup_proc.returncode} ##########")

    # Check late for errors, to maximize the printed output.
    for path_exp, cur, exp in pairs:
        assert cur == exp, path_exp

    assert stepup_proc.returncode == 0

    # `stepup build` scans its own log when it exits and fails the build over any finding,
    # because `STEPUP_DEBUG` is set above. This second scan is the safety net for the runs
    # that never got that far, e.g. because the example killed StepUp.
    # It only covers the last `stepup build` of the example: each one truncates the log.
    findings = scan_director_log(workdir / DIRECTOR_LOG)
    assert not findings, "Problems in director.log:\n" + "\n".join(findings)


async def run_plan(srcdir: Path, tmpdir: Path):
    """Copy a `plan.py` script to a temporary directory and run it as an ordinary Python script.

    Parameters
    ----------
    srcdir
        The source directory of the example, with a `plan.py` script.
    tmpdir
        The temporary directory to use.

    Notes
    -----
    This is not the intended way of using `plan.py` scripts.
    They are normally processed by StepUp instead of being run directly.
    Nevertheless, running them directly should not raise exceptions,
    which is useful for debugging.
    """
    workdir = tmpdir / "example"
    shutil.copytree(srcdir, workdir)
    plan_proc = await asyncio.create_subprocess_shell(
        f"{sys.executable} plan.py",
        stdin=subprocess.DEVNULL,
        cwd=workdir,
        env=os.environ | {"PYTHONUNBUFFERED": "yes"},
    )
    await plan_proc.wait()
    assert plan_proc.returncode == 0


@functools.cache
def _parse(path: Path) -> ast.Module:
    """The abstract syntax tree of a Python source file."""
    return ast.parse(path.read_text(), filename=path)


@functools.cache
def _find_module_paths(package: str) -> tuple[Path, ...]:
    """The source files of the top-level modules of a package, sorted by name.

    Raises
    ------
    ModuleNotFoundError
        When the package cannot be imported.
    """
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(f"Not an importable package: {package}")
    return tuple(sorted(Path(spec.submodule_search_locations[0]).glob("*.py")))


@functools.cache
def _find_module_path(module: str) -> Path | None:
    """The source file of a module, or `None` when it is not a Python module.

    A package is also rejected, because `from package import name` may import a submodule,
    which is not part of the `__all__` contract of the package's `__init__.py`.
    """
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return None
    if spec is None or spec.submodule_search_locations is not None:
        return None
    if spec.origin is None or not spec.origin.endswith(".py"):
        return None
    return Path(spec.origin)


@functools.cache
def _get_dunder_all(path: Path) -> tuple[str, ...] | None:
    """The `__all__` tuple of a module, or `None` when it is not declared."""
    for node in _parse(path).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            if not isinstance(node.value, ast.Tuple):
                raise TypeError("__all__ must be a tuple")
            return tuple(element.value for element in node.value.elts)
    return None


class ConventionTests:
    """Tests for coding conventions that ruff cannot express, shared by all StepUp packages.

    Subclass this in a test module, under a name that pytest collects,
    and point `package` at the package to be tested:

    ```python
    from stepup.core.pytest import ConventionTests


    class TestConventions(ConventionTests):
        package = "stepup.spam"
    ```

    For the assertions to explain themselves when they fail,
    the test suite's `conftest.py` must contain:

    ```python
    pytest.register_assert_rewrite("stepup.core.pytest")
    ```
    """

    package: str | None = None
    """The dotted name of the package whose top-level modules are tested."""

    example_rc: str | None = "examples/example.rc"
    """The shell boilerplate of the integration examples, relative to the test module.

    Set this to `None` when the test suite has no integration examples.
    """

    def pytest_generate_tests(self, metafunc):
        """Parametrize the per-module tests over the top-level modules of `package`."""
        if "module_path" in metafunc.fixturenames:
            if self.package is None:
                raise ValueError(f"{type(self).__name__} does not define the package to test.")
            paths = _find_module_paths(self.package)
            metafunc.parametrize("module_path", paths, ids=[path.name for path in paths])

    def test_dunder_all_declared(self, module_path: Path):
        """Every module declares `__all__`, even when it exports nothing."""
        assert _get_dunder_all(module_path) is not None, (
            f"{module_path.name} does not declare __all__"
        )

    def test_dunder_all_defined_here(self, module_path: Path):
        """Names in `__all__` are defined in the module itself, not re-exported from another one."""
        imported = set()
        defined = set()
        for node in ast.walk(_parse(module_path)):
            if isinstance(node, ast.ImportFrom | ast.Import):
                imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
        for name in _get_dunder_all(module_path) or ():
            assert name in defined or name not in imported, (
                f"{module_path.name} re-exports {name}: "
                f"import it from the module that defines it instead"
            )

    def test_imports_are_exported(self, module_path: Path):
        """A name imported from another StepUp module must appear in that module's `__all__`.

        There is no exemption for underscore-prefixed names:
        reaching into another module's internals is exactly what this check is meant to prevent.
        Move the name to a module both parties may depend on,
        or promote it to part of the defining module's contract.
        """
        for node in ast.walk(_parse(module_path)):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.level == 1:
                module = f"{self.package}.{node.module}"
            elif node.level == 0 and node.module.startswith("stepup."):
                module = node.module
            else:
                continue
            path = _find_module_path(module)
            if path is None:
                continue
            exported = _get_dunder_all(path)
            assert exported is not None, (
                f"{module_path.name} imports from {module}, which has no __all__"
            )
            for alias in node.names:
                assert alias.name in exported, (
                    f"{module_path.name} imports {alias.name} from {module}, "
                    f"which does not list it in __all__"
                )

    def test_example_return_code_constants(self, request):
        """The `RETURN_CODE_*` constants in `example.rc` match the `ReturnCode` enum.

        The integration examples assert exit codes through these shell constants,
        so a renumbered or renamed flag must not silently leave them behind:
        the examples would keep passing while testing the wrong bit.
        """
        if self.example_rc is None:
            pytest.skip("The test suite has no integration examples.")
        text = (Path(request.path).parent / self.example_rc).read_text()
        constants = {
            match.group(1): int(match.group(2))
            for match in re.finditer(r"^RETURN_CODE_(\w+)=(\d+)$", text, re.MULTILINE)
        }
        assert constants == {flag.name: flag.value for flag in ReturnCode}
