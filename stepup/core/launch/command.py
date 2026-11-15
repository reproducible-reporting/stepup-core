# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Classify the command of a step and launch it in the right way."""

import functools
import logging
import multiprocessing
import shlex
import shutil
import sys
import sysconfig
from importlib.metadata import entry_points

import attrs

from ..exceptions import RunError
from ..graph.outcome import ChildOutcome
from ..path import Path
from .copy import is_builtin_copy, run_builtin_copy
from .forkserver import exec_in_forkserver, forkserver_entry
from .subprocess import check_executable, run_subprocess
from .worker import Run

__all__ = ("launch_command",)

logger = logging.getLogger(__name__)


#
# Entry point detection
#


@functools.cache
def _get_console_script_entry_points():
    """Return the `console_scripts` entry points of all installed distributions, scanned once.

    Returns
    -------
    eps
        An `EntryPoints` collection, cheap to filter further with `.select(name=...)`.
    """
    return entry_points(group="console_scripts")


def _executable_uses_same_python(path_exec: str) -> bool:
    """Check if an executable script's shebang resolves to the same Python as the current process.

    This detects console_script wrappers installed in PATH-extended locations
    (e.g. additional environment modules loaded on top of the base Python module)
    whose shebang points to the same Python executable as `sys._base_executable`.
    When true, the executable and the forkserver use the same interpreter
    and inherit the same environment variables, so their behavior is equivalent.

    Parameters
    ----------
    path_exec
        Path to the executable script to inspect.

    Returns
    -------
    same
        `True` when the script's shebang resolves to the same interpreter as
        `sys._base_executable`, `False` for non-scripts or a different interpreter.
    """
    base_exec = Path(sys._base_executable).realpath()
    try:
        with open(path_exec, "rb") as f:
            head = f.read(256)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    try:
        shebang = head.split(b"\n")[0][2:].decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    parts = shebang.split()
    if not parts:
        return False
    # Handle both `#!/path/to/python` and `#!/usr/bin/env python3` forms.
    if Path(parts[0]).name == "env" and len(parts) > 1:
        python_on_path = shutil.which(parts[1])
        if python_on_path is None:
            return False
        shebang_python = Path(python_on_path).realpath()
    else:
        shebang_python = Path(parts[0]).realpath()
    return shebang_python == base_exec


def _executable_compatible_with_current_python(which_path: str) -> bool:
    """Check if an executable is usable with the current Python environment.

    Fast path: the executable lives inside the current environment's directory for scripts
    (`bin`, or `Scripts` on Windows), or that of the base environment it was created from.
    Slow path: the executable's shebang resolves to the same Python interpreter,
    covering packages installed via PYTHONPATH-extending environment modules.

    Parameters
    ----------
    which_path
        Path to the executable to check, e.g. as returned by `shutil.which`.

    Returns
    -------
    compatible
        `True` when the executable is inside the current Python environment,
        or its shebang resolves to the same interpreter as `sys._base_executable`.
        `False` otherwise.
    """
    resolved = Path(which_path).realpath()
    # Only the name of the directory is taken from `sysconfig`,
    # whose full path may be one that a distribution reserves for locally installed packages.
    scripts = Path(sysconfig.get_path("scripts")).name
    prefixes = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    env_bins = {(Path(prefix) / scripts).realpath() for prefix in prefixes}
    path_ok = any(resolved.startswith(d / "") or resolved == d for d in env_bins)
    return path_ok or _executable_uses_same_python(which_path)


_PYTHON_ENTRYPOINT_CACHE: dict[str, tuple[str | None, str]] = {}


def _detect_python_entrypoint(cmd: str) -> tuple[str | None, str]:
    """Detect if `cmd` is a console_script compatible with the current Python environment.

    Parameters
    ----------
    cmd
        The bare command name (no path separators) to look up.

    Returns
    -------
    ep_value
        The entry point value string (e.g. `"pytest:main"`) when `cmd` is a console_script
        importable in and compatible with the current Python environment, or `None` otherwise.
    warning
        A message explaining why a console_script was rejected,
        or an empty string when there is nothing to report.
        The message is repeated for every detection of the same `cmd`,
        so that each affected step reports it,
        independently of the order in which steps are scheduled.

    Raises
    ------
    RunError
        When `cmd` is registered as a console_script but cannot be found on `PATH`,
        which indicates a broken installation.
    """
    if cmd in _PYTHON_ENTRYPOINT_CACHE:
        return _PYTHON_ENTRYPOINT_CACHE[cmd]
    ep_value = None
    warning = ""
    eps = list(_get_console_script_entry_points().select(name=cmd))
    if len(eps) > 0:
        which_path = shutil.which(cmd)
        if which_path is None:
            # Deliberately not cached,
            # so a repaired installation is picked up without restarting the director.
            raise RunError(
                f"Command '{cmd}' is registered as a Python console_script entry point "
                "but was not found on PATH. The installation may be broken."
            )
        if _executable_compatible_with_current_python(which_path):
            ep_value = eps[0].value
        else:
            warning = (
                f"WARNING: Entry point '{cmd}' ('{which_path}') is outside the current"
                f" Python environment ({sys.prefix}). Running it as a subprocess.\n"
            )
    _PYTHON_ENTRYPOINT_CACHE[cmd] = (ep_value, warning)
    return ep_value, warning


PYCODE_WRAPPER = """\
import os
import sys
import runpy
from stepup.core.path import Path
from stepup.core.api import amend
from stepup.core.extapi import get_local_import_paths
sys.argv = {argv}
try:
    runpy.run_path({script}, run_name="__main__")
finally:
    amend(inp=get_local_import_paths(script_path=Path({script})))
"""


async def _run_python_script(
    script: str,
    args: list[str],
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Run a Python script, amending its local imports as inputs."""
    message = check_executable(cwd / Path(script), shebang="#!/usr/bin/env python3")
    if message is not None:
        return ChildOutcome(1, "", message + "\n")
    if mp_ctx is not None:
        return await exec_in_forkserver(
            mp_ctx, forkserver_entry, (script, args, env, str(cwd), None), run
        )
    wrapper = PYCODE_WRAPPER.format(argv=repr([script, *args]), script=repr(script))
    return await run_subprocess(
        [sys.executable, "-"], shell=False, env=env, cwd=cwd, run=run, stdin_data=wrapper.encode()
    )


async def _run_python_entrypoint(
    cmd: str,
    args: list[str],
    ep_value: str,
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Run a Python console_script entry point, using the forkserver when available."""
    if mp_ctx is not None:
        return await exec_in_forkserver(
            mp_ctx, forkserver_entry, (cmd, args, env, str(cwd), ep_value), run
        )
    return await run_subprocess([cmd, *args], shell=False, env=env, cwd=cwd, run=run)


#
# Command dispatching
#


async def launch_command(
    command: str,
    *,
    shell: bool,
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Launch a step's command and return its `ChildOutcome`.

    Dispatches to a shell, a Python script (`*.py`), a Python console_script entry point,
    or a plain (non-shell) exec.
    Python scripts and entry points run in a forkserver child when `mp_ctx` is not `None`,
    and as a plain subprocess otherwise.
    The one command that starts no process at all is `cp -p SRC DST` without a shell:
    the director copies the file itself,
    because starting a process for it is slow on platforms without a forkserver,
    and because `cp` does not exist on Windows.

    Parameters
    ----------
    command
        The command line to launch, as it would be typed in a shell.
    shell
        Whether to run `command` through a shell instead of splitting and exec'ing it directly.
    env
        The environment variables for the child process.
    cwd
        The working directory for the child process.
    mp_ctx
        The forkserver multiprocessing context, or `None` to use plain subprocesses.
    run
        The `Run` whose `worker` attribute is set to the launched child while it is in flight.

    Returns
    -------
    outcome
        The `ChildOutcome` of the launched command.

    Raises
    ------
    ValueError
        When `command` is empty, which is a programming error in the director.
    """
    parts = shlex.split(command)
    if not parts:
        raise ValueError(f"Empty command: {command!r}")
    if not shell and is_builtin_copy(parts):
        return await run_builtin_copy(parts[2], parts[3], cwd, run)
    if shell:
        return await run_subprocess(command, shell=True, env=env, cwd=cwd, run=run)
    if parts[0].endswith(".py"):
        return await _run_python_script(parts[0], parts[1:], env, cwd, mp_ctx, run)
    try:
        ep_value, warning = _detect_python_entrypoint(parts[0])
    except RunError as exc:
        return ChildOutcome(1, "", str(exc) + "\n")
    if ep_value is None:
        outcome = await run_subprocess(parts, shell=False, env=env, cwd=cwd, run=run)
    else:
        outcome = await _run_python_entrypoint(parts[0], parts[1:], ep_value, env, cwd, mp_ctx, run)
    if len(warning) > 0:
        outcome = attrs.evolve(outcome, stderr=warning + outcome.stderr)
    return outcome
