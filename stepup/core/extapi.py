# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities for developers of StepUp extension packages.

These functions are not intended for end users writing `plan.py` files.
They are meant for authors of new StepUp extensions who need to interact with the director,
filter step dependencies, or implement custom API functions that handle environment variables.
"""

import hashlib
import os
import shlex
import subprocess
import sys
from collections.abc import Iterable

from path import Path

from .api import get_job_i, get_rpc_client, getenv
from .exceptions import ConsistencyError, StepUpError
from .path import StrPath, get_stepup_root, translate
from .step import truncate_output
from .utils import extract_env_overrides

__all__ = (
    "filter_dependencies",
    "get_local_import_paths",
    "record_subprocess",
    "run_subprocess",
)


def _prepare_stream(data: str | bytes | None, max_bytes: int = 0) -> str:
    """Return a text representation of `data` suitable for the archival record.

    `None` becomes an empty string.

    A `str` remains unchanged if `max_bytes` is `0` (unlimited)
    or the string fits within `max_bytes` UTF-8 bytes.
    Otherwise, the string is truncated to `max_bytes` bytes
    and a note is appended to indicate that it was truncated.

    `bytes` are summarized into a short, human-readable placeholder
    (byte length and a truncated SHA-256),
    because the archival columns (`stdin`, `stdout`, `stderr`) are `TEXT`
    and a raw binary blob is neither valid UTF-8
    nor meaningful to a human inspecting the database.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return truncate_output(data, max_bytes)
    if isinstance(data, bytes):
        digest = hashlib.sha256(data).hexdigest()[:16]
        return f"<{len(data)} bytes of binary data, sha256={digest}>"
    raise TypeError(f"data must be str, bytes, or None, not {type(data).__name__}")


def record_subprocess(
    cmd: str,
    returncode: int,
    *,
    workdir: StrPath = ".",
    env_overrides: dict[str, str] | None = None,
    shell: bool = False,
    stdin: str | bytes | None = None,
    stdout: str | bytes | None = None,
    stderr: str | bytes | None = None,
) -> None:
    """Record a subprocess invocation (already run by the caller) for archival purposes.

    This is the low-level escape hatch for wrappers that run the subprocess themselves
    (e.g. for streaming output, `Popen`-style pipe interaction, shell features,
    or conditional invocations).

    Most wrappers should use `run_subprocess` instead.

    The recorded metadata is meant to be informative for archival and debugging, not authoritative.
    Outside a running step (when `STEPUP_JOB_I` is unset and the RPC client is the dummy one),
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
        The environment **overlay** that the caller applied on top of the inherited environment
        (only the variables it explicitly set), or `None`.
        Only this overlay is stored, not the full resolved environment.
    shell
        Whether `cmd` was executed via a shell (i.e. `subprocess.run(..., shell=True)`).
        This is stored and used when formatting the invocation for display.
    stdin, stdout, stderr
        The standard input/output/error of the subprocess, or `None` when not captured.
        A `str` is stored verbatim, subject to the `STEPUP_MAX_OUTPUT_SIZE` cap.
        `bytes` (e.g. a pickle blob) are not stored raw:
        they are recorded as a short summary (byte length and a truncated SHA-256),
        since the archival record is `TEXT` and informative rather than authoritative.
    """
    job_i = get_job_i()
    if job_i < 0:
        return
    max_output_size = int(os.getenv("STEPUP_MAX_OUTPUT_SIZE", "0"))
    get_rpc_client().call.record_subprocess(
        job_i,
        cmd,
        translate(workdir),
        env_overrides,
        returncode,
        shell,
        _prepare_stream(stdin, max_output_size),
        _prepare_stream(stdout, max_output_size),
        _prepare_stream(stderr, max_output_size),
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
    The invocation, its return code, and its captured stdout/stderr
    are recorded via `record_subprocess` (subject to the `STEPUP_MAX_OUTPUT_SIZE` cap).

    Parameters
    ----------
    cmd
        The command line, as a single shell-quoted string.
        When `shell=False` (the default),
        `cmd` is split with `shlex.split` and executed directly (no shell),
        so shell features (pipes, redirections, ...) are not available.
        When `shell=True`, `cmd` is passed as-is to the system shell, which enables shell features.
        As an exception, leading `VAR=value` assignments are extracted and applied
        to the subprocess environment, even when `shell=False`.
        In either case, the caller is responsible for proper quoting.
    workdir
        The working directory of the subprocess as a path or string,
        relative to the step's own working directory.
        It is passed to `subprocess.run` as `cwd`.
    stdin
        Standard input fed to the subprocess, or `None`.
        A `str` is passed to `subprocess.run` as-is and implies `text=True`.
        `bytes` are passed as-is as well and imply `text=False`.
        Inconsistent combinations (e.g. `stdin` is `bytes` but `text=True`) raise a `TypeError`.
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
        Whether to run in text or binary mode.
        By default, the mode follows the type of `stdin`: text for `str`, binary for `bytes`.
        When no `stdin` is provided, the default is text mode.

    Returns
    -------
    completed
        The `subprocess.CompletedProcess` returned by `subprocess.run`.

    Raises
    ------
    subprocess.CalledProcessError
        When `check` is `True` and the subprocess exits with a non-zero return code.
    TypeError
        When `stdin` is not `str`, `bytes`, or `None`,
        or when `stdin` is inconsistent with the `text` flag.
    """
    if shell:
        env_overrides = None
    else:
        env_overrides, cmd = extract_env_overrides(cmd)
    run_env = dict(os.environ)
    if env_overrides is not None:
        run_env.update(env_overrides)
    run_kwargs = {
        "cwd": workdir,
        "env": run_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,  # handled below, so the subprocess can be recorded with its return code
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
                raise TypeError("stdin must be bytes when text=False")
        elif isinstance(stdin, bytes):
            if text is None:
                text = False
            elif text is True:
                raise TypeError("stdin must be str when text=True")
        else:
            raise TypeError("stdin must be str, bytes, or None")
    if text is None:
        text = True
    run_kwargs["text"] = text
    if text:
        # Useful to deal with ill-behaved subprocesses like LaTeX.
        run_kwargs["errors"] = "ignore"
    if shell:
        cp = subprocess.run(cmd, **run_kwargs)  # noqa: PLW1510
    else:
        argv = shlex.split(cmd)
        cp = subprocess.run(argv, **run_kwargs)  # noqa: PLW1510
    record_subprocess(
        cmd,
        cp.returncode,
        workdir=workdir,
        env_overrides=env_overrides,
        shell=shell,
        stdin=stdin,
        stdout=cp.stdout,
        stderr=cp.stderr,
    )
    if check and cp.returncode != 0:
        if cp.stdout:
            sys.stdout.write(cp.stdout if text else cp.stdout.decode())
        if cp.stderr:
            sys.stderr.write(cp.stderr if text else cp.stderr.decode())
        raise subprocess.CalledProcessError(cp.returncode, cmd, cp.stdout, cp.stderr)
    return cp


def filter_dependencies(paths: Iterable[StrPath]) -> set[Path]:
    """Select the paths retained by `${STEPUP_PATH_FILTER}`.

    Parameters
    ----------
    paths
        An iterable of paths or strings to filter.
        Relative paths are assumed to be relative to the current working directory.

    Returns
    -------
    filtered_paths
        A collection of paths retained by the filter,
        relative to the current working directory.

    Raises
    ------
    StepUpError
        When `${STEPUP_PATH_FILTER}` contains an item that does not start with `+` or `-`.
    """
    # Parse the ${STEPUP_PATH_FILTER} environment variable.
    # The getenv function from StepUp amends the current step to depend on the variable,
    # so that every step using it is re-executed when the variable changes.
    filter_str = getenv("STEPUP_PATH_FILTER", "-venv")
    filter_str += ":+.:-/"
    rules = []
    stepup_root = get_stepup_root()
    for filter_item in filter_str.split(":"):
        if filter_item == "":
            continue
        if filter_item.startswith("+"):
            keep = True
        elif filter_item.startswith("-"):
            keep = False
        else:
            raise StepUpError(f"Invalid filter item: {filter_item}")
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
            raise ConsistencyError(f"No matching rule found for path: {path}")
    return result


def get_local_import_paths(script_path: StrPath | None = None) -> list[Path]:
    """Get all local files from `sys.modules`.

    Parameters
    ----------
    script_path
        The path of the script that is currently running, or `None` if unknown.

    Returns
    -------
    local_paths
        A sorted list of paths to local files that are currently imported in `sys.modules`.

    Notes
    -----
    Files are only included if they match the `${STEPUP_PATH_FILTER}` environment variable.
    Non-existent files are ignored:
    they can only be the result of a dynamically created module,
    as in issue <https://github.com/reproducible-reporting/stepup-core/issues/21>.
    There is no risk of missing files that still need to be created,
    as all imports have already been successfully resolved at this point.
    """

    def iter_module_paths():
        for module in sys.modules.values():
            mod_path = getattr(module, "__file__", None)
            if not (mod_path is None or mod_path.startswith("<")):
                mod_path = Path(mod_path).normpath()
                if mod_path.exists():
                    yield mod_path

    mod_paths = filter_dependencies(iter_module_paths())
    # The script itself is not returned because it is an input of the step by construction.
    if script_path is not None:
        mod_paths.discard(Path(script_path).normpath())
    return sorted(mod_paths)
