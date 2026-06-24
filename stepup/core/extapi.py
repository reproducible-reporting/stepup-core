# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Utilities for developers of StepUp extension packages.

These functions are not intended for end-users writing `plan.py` files.
They are meant for authors of new StepUp extensions who need to interact with the director,
filter step dependencies, or implement custom API functions that handle environment variables.
"""

import contextlib
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Collection, Iterator

from path import Path

from .rpc import SocketSyncRPCClient
from .utils import CaseSensitiveTemplate, translate

__all__ = (
    "filter_dependencies",
    "get_local_import_paths",
    "record_subprocess",
    "run_subprocess",
    "subs_env_vars",
)


@contextlib.contextmanager
def subs_env_vars() -> Iterator[Callable[[str | None], Path | None]]:
    """A context manager for substituting environment variables and tracking the used variables.

    The context manager yields a function, `subs`, which takes a string with variables and
    returns the substituted form.
    All used variables are recorded and sent to the director with `amend(env=...)`.
    For example:

    ```python
    with subs_env_vars() as subs:
        path_inp = subs(path_inp)
        path_out = subs(path_out)
    ```

    This function may be used in other API functions to substitute environment variables in
    all relevant paths.
    """
    from stepup.core.api import amend  # noqa: PLC0415

    env_vars = set()

    def subs(path: str | None) -> Path | None:
        if path is None:
            return None
        template = CaseSensitiveTemplate(path)
        if not template.is_valid():
            raise ValueError("The path contains invalid shell variable identifiers.")
        mapping = {}
        for name in template.get_identifiers():
            if name.startswith("*"):
                mapping[name] = f"${{{name}}}"
            else:
                value = os.getenv(name)
                if value is None:
                    raise ValueError(f"Undefined shell variable: {name}")
                mapping[name] = value
                env_vars.add(name)
        return Path(path if len(mapping) == 0 else template.substitute(mapping))

    yield subs
    amend(env=env_vars)


def record_subprocess(
    cmd: str,
    returncode: int,
    *,
    workdir: str = ".",
    env: dict[str, str] | None = None,
    shell: bool = False,
) -> None:
    """Record a subprocess invocation (already run by the caller) for archival purposes.

    This is the low-level escape hatch for wrappers that run the subprocess themselves
    (e.g. for streaming output, `Popen`-style pipe interaction, shell features, or
    conditional invocations).

    Most wrappers should use `run_subprocess` instead.

    The recorded metadata is meant to be informative for archival and debugging, not authoritative.
    Outside a running step (e.g. under the dummy RPC client used in tests or driver code),
    this function is a no-op.

    Parameters
    ----------
    cmd
        The command line, as a single shell-quoted string.
        The caller is responsible for quoting: build it from parts with `shlex.join(parts)`
        when arguments may contain spaces or special characters.
        The string is stored and displayed verbatim.
    returncode
        The exit code of the subprocess.
    workdir
        The working directory of the subprocess, relative to the step's own working directory.
        It is translated to be relative to `STEPUP_ROOT` for storage.
    env
        The environment **overlay** that the caller applied on top of the inherited
        environment (only the variables it explicitly set), or `None`. Only this overlay
        is stored, not the full resolved environment.
    shell
        Whether `cmd` was executed via a shell (i.e. `subprocess.run(..., shell=True)`).
        This is stored and used when formatting the invocation for display.
    """
    from stepup.core.api import RPC_CLIENT, _get_step_i  # noqa: PLC0415

    if isinstance(RPC_CLIENT, SocketSyncRPCClient):
        step_i = _get_step_i()
        RPC_CLIENT.call.record_subprocess(step_i, cmd, translate(workdir), env, returncode, shell)


def run_subprocess(
    cmd: str,
    *,
    workdir: str = ".",
    env: dict[str, str] | None = None,
    stdout=None,
    stderr=None,
    check: bool = True,
    shell: bool = False,
) -> subprocess.CompletedProcess:
    """Run a subprocess and record it for archival purposes.

    This is the convenience wrapper for the case where an extension step wraps an executable.
    The invocation and its return code are recorded via `record_subprocess`.

    Parameters
    ----------
    cmd
        The command line, as a single shell-quoted string.
        When `shell=False` (the default), `cmd` is split with `shlex.split` and executed
        directly (no shell), so shell features (pipes, redirections, ...) are not available.
        When `shell=True`, `cmd` is passed as-is to the system shell, which enables shell features.
        In either case, the caller is then responsible for proper quoting.
    workdir
        The working directory of the subprocess, relative to the step's own working directory.
        It is passed to `subprocess.run` as `cwd`.
    env
        An environment **overlay**, merged over `os.environ` for execution
        (so passing `env={"FOO": "bar"}` adds `FOO` without dropping `PATH` and the rest).
        Only this overlay is recorded, not the full resolved environment.
        When `None`, the environment is inherited unchanged and nothing is recorded for it.
    stdout, stderr
        Passed through to `subprocess.run`. When left at their default (`None`), output
        goes wherever the step process's own file descriptors point.
    check
        When `True`, a `subprocess.CalledProcessError` is raised on a non-zero exit code.
        The invocation is recorded **before** this check, so a failing subprocess is still archived.
    shell
        When `True`, execute `cmd` via the system shell (`subprocess.run(..., shell=True)`).
        Enables shell features such as pipes, redirections, and glob expansion.
        The flag is also recorded for display purposes.

    Returns
    -------
    completed
        The `subprocess.CompletedProcess` returned by `subprocess.run`.

    Raises
    ------
    subprocess.CalledProcessError
        When `check` is `True` and the subprocess exits with a non-zero return code.
    """
    run_env = None if env is None else {**os.environ, **env}
    # Flush so already-buffered parent output is written before the subprocess (which inherits
    # our file descriptors when stdout/stderr are None) can write to the same streams.
    sys.stdout.flush()
    sys.stderr.flush()
    if shell:
        cp = subprocess.run(
            cmd, cwd=workdir, env=run_env, stdout=stdout, stderr=stderr, check=False, shell=True
        )
    else:
        argv = shlex.split(cmd)
        cp = subprocess.run(
            argv, cwd=workdir, env=run_env, stdout=stdout, stderr=stderr, check=False
        )
    record_subprocess(cmd, cp.returncode, workdir=workdir, env=env, shell=shell)
    if check and cp.returncode != 0:
        raise subprocess.CalledProcessError(cp.returncode, cmd, cp.stdout, cp.stderr)
    return cp


def filter_dependencies(paths: Collection[str]) -> set[Path]:
    """Select path retained by the `${STEPUP_PATH_FILTER}`.

    Parameters
    ----------
    paths
        A collection of paths to filter.
        Relative paths are assumed to be relative to the current working directory.

    Returns
    -------
    filtered_paths
        A collection of paths relative to `${STEPUP_ROOT}` that were retained by the filter.
    """
    # The getenv function from StepUp amends the current step to depend on the variable,
    # to make sure that all steps using it get re-executed properly.
    from stepup.core.api import getenv  # noqa: PLC0415

    # Parse the ${STEPUP_PATH_FILTER} environment variable.
    filter_str = getenv("STEPUP_PATH_FILTER", "-venv")
    filter_str += ":+.:-/"
    rules = []
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
    for filter_item in filter_str.split(":"):
        if filter_item == "":
            continue
        if filter_item.startswith("+"):
            keep = True
        elif filter_item.startswith("-"):
            keep = False
        else:
            raise ValueError(f"Invalid filter item: {filter_item}")
        prefix = Path(filter_item[1:])
        if not prefix.isabs():
            prefix = (stepup_root / prefix).realpath()
        rules.append((prefix, keep))

    # Filter paths according to the rules.
    result = set()
    realpwd = Path.cwd().realpath()
    for path in paths:
        abspath = Path(path).realpath()
        for prefix, keep in rules:
            if abspath.startswith(prefix):
                if keep:
                    result.add(abspath.relpath(realpwd))
                break
        else:
            raise AssertionError(f"No matching rule found for path: {path}")
    return result


def get_local_import_paths(script_path: str | None = None) -> list[str]:
    """Get all local files from `sys.modules`.

    Files are only included if they match the `${STEPUP_PATH_FILTER}` environment variable.
    Non-existing files will be ignored, as they can only be the result of a dynamically created
    module, as in issue https://github.com/reproducible-reporting/stepup-core/issues/21
    There is no risk of missing files that still need to be created,
    as all imports have already been successfully resolved already at this point.
    """

    def iter_module_paths():
        for module in sys.modules.values():
            mod_path = getattr(module, "__file__", None)
            if not (mod_path is None or mod_path.startswith("<")):
                mod_path = Path(mod_path).normpath()
                if mod_path.exists():
                    yield mod_path

    mod_paths = filter_dependencies(iter_module_paths())
    # The script path is already included in the inputs.
    if script_path is not None:
        mod_paths.discard(Path(script_path).normpath())
    return sorted(mod_paths)
