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
import hashlib
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Collection, Iterator

from path import Path

from .path import StrPath, coerce_str, translate
from .rpc import SocketSyncRPCClient
from .utils import CaseSensitiveTemplate

__all__ = (
    "filter_dependencies",
    "get_local_import_paths",
    "record_subprocess",
    "run_subprocess",
    "subs_env_vars",
)


def _summarize_binary_stdin(stdin: str | bytes | None) -> str | None:
    """Return a text representation of `stdin` suitable for the archival record.

    `str` and `None` pass through unchanged. `bytes` are summarized into a short,
    human-readable placeholder (byte length and a truncated SHA-256), because the
    archival `stdin` column is `TEXT` and a raw binary blob is neither valid UTF-8
    nor meaningful to a human inspecting the database.
    """
    if isinstance(stdin, bytes):
        digest = hashlib.sha256(stdin).hexdigest()[:16]
        return f"<{len(stdin)} bytes of binary stdin, sha256={digest}>"
    return stdin


@contextlib.contextmanager
def subs_env_vars() -> Iterator[Callable[[StrPath | None], Path | None]]:
    """A context manager for substituting environment variables and tracking the used variables.

    The context manager yields a function, `subs`, which takes a path or string with variables and
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

    used_env = set()

    def subs(path: StrPath | None) -> Path | None:
        if path is None:
            return None
        template = CaseSensitiveTemplate(coerce_str(path))
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
                used_env.add(name)
        return Path(path if len(mapping) == 0 else template.substitute(mapping))

    yield subs
    amend(env=used_env)


def record_subprocess(
    cmd: str,
    returncode: int,
    *,
    workdir: StrPath = ".",
    env_overrides: dict[str, str] | None = None,
    shell: bool = False,
    stdin: str | bytes | None = None,
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
        The working directory of the subprocess as a path or string,
        relative to the step's own working directory.
        It is translated to be relative to `STEPUP_ROOT` for storage.
    env_overrides
        The environment **overlay** that the caller applied on top of the inherited
        environment (only the variables it explicitly set), or `None`. Only this overlay
        is stored, not the full resolved environment.
    shell
        Whether `cmd` was executed via a shell (i.e. `subprocess.run(..., shell=True)`).
        This is stored and used when formatting the invocation for display.
    stdin
        The standard input that was fed to the subprocess, or `None`.
        A `str` is stored verbatim. `bytes` (e.g. a pickle blob) are not stored raw;
        they are recorded as a short summary (byte length and a truncated SHA-256),
        since the archival record is `TEXT` and informative rather than authoritative.
    """
    from stepup.core.api import RPC_CLIENT, _get_step_i  # noqa: PLC0415

    if isinstance(RPC_CLIENT, SocketSyncRPCClient):
        step_i = _get_step_i()
        RPC_CLIENT.call.record_subprocess(
            step_i,
            cmd,
            translate(workdir),
            env_overrides,
            returncode,
            shell,
            _summarize_binary_stdin(stdin),
        )


def run_subprocess(
    cmd: str,
    *,
    workdir: StrPath = ".",
    stdin: str | bytes | None = None,
    shell: bool = False,
    check: bool = True,
    text: bool | None = None,
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
        As an exception, leading `VAR=value` assignments are extracted and applied
        to the subprocess environment, even when `shell=False`.
        In either case, the caller is then responsible for proper quoting.
    workdir
        The working directory of the subprocess as a path or string,
        relative to the step's own working directory.
        It is passed to `subprocess.run` as `cwd`.
    stdin
        Standard input fed to the subprocess, or `None`.
        A `str` is passed to `subprocess.run` as-is and implies `text=True`.
        `bytes` are passed as-is as well and imply `text=False`.
        Inconsistent combinations (e.g. `stdin` is `bytes` but `text=True`) raise a `ValueError`.
        The value is forwarded to `record_subprocess`, which stores `bytes` as a short summary
        (byte length and a truncated SHA-256) rather than raw binary.
    shell
        When `True`, execute `cmd` via the system shell (`subprocess.run(..., shell=True)`).
        Enables shell features such as pipes, redirections, and glob expansion.
        The flag is also recorded for display purposes.
    check
        When `True`, a `subprocess.CalledProcessError` is raised on a non-zero exit code.
        The invocation is recorded **before** this check, so a failing subprocess is still archived.
        In case of such a failure, the subprocess's standard output and error are printed
        to the caller's standard output and error stream.
    text
        The default is to follow the type of `stdin` to run in text or binary mode.
        If no `stdin` is provided, the default is text mode.

    Returns
    -------
    completed
        The `subprocess.CompletedProcess` returned by `subprocess.run`.

    Raises
    ------
    subprocess.CalledProcessError
        When `check` is `True` and the subprocess exits with a non-zero return code.
    """
    if not shell:
        from stepup.core.api import _extract_env_overrides  # noqa: PLC0415

        env_overrides, cmd = _extract_env_overrides(cmd)
    else:
        env_overrides = None
    run_env = dict(os.environ)
    if env_overrides is not None:
        run_env.update(env_overrides)
    # Build a dict of keyword arguments to pass to `subprocess.run`.
    run_kwargs = {
        "cwd": workdir,
        "env": run_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,  # handled below, so we can record the subprocess with returncode
        "shell": shell,
    }
    if stdin is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = stdin
        if isinstance(stdin, str):
            if text is None:
                text = True
            elif text is False:
                raise ValueError("stdin must be bytes when text=False")
        elif isinstance(stdin, bytes):
            if text is None:
                text = False
            elif text is True:
                raise ValueError("stdin must be str when text=True")
        else:
            raise TypeError("stdin must be str, bytes, or None")
    if text is None:
        text = True
    run_kwargs["text"] = text
    if shell:
        cp = subprocess.run(cmd, **run_kwargs)  # noqa: PLW1510
    else:
        argv = shlex.split(cmd)
        cp = subprocess.run(argv, **run_kwargs)  # noqa: PLW1510
    record_subprocess(
        cmd, cp.returncode, workdir=workdir, env_overrides=env_overrides, shell=shell, stdin=stdin
    )
    if check and cp.returncode != 0:
        if cp.stdout:
            sys.stdout.write(cp.stdout if text else cp.stdout.decode())
        if cp.stderr:
            sys.stderr.write(cp.stderr if text else cp.stderr.decode())
        raise subprocess.CalledProcessError(cp.returncode, cmd, cp.stdout, cp.stderr)
    return cp


def filter_dependencies(paths: Collection[StrPath]) -> set[Path]:
    """Select path retained by the `${STEPUP_PATH_FILTER}`.

    Parameters
    ----------
    paths
        A collection of paths or strings to filter.
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


def get_local_import_paths(script_path: StrPath | None = None) -> list[Path]:
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
