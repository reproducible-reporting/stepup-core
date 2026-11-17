# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run a Python script or entry point of a step in a forkserver child."""

import atexit
import contextlib
import functools
import importlib
import io
import logging
import multiprocessing
import os
import runpy
import signal
import sys
import time

from ..api import amend
from ..extapi import get_local_import_paths
from ..graph.outcome import ChildOutcome, ResourceUsage
from ..path import Path
from ..platform.asyncio import wait_for_readable_fd
from ..tracebacks import print_step_traceback
from .capture import decode_output, normalize_newlines, start_drain
from .worker import ForkserverWorker, Run

if sys.platform != "win32":
    # Windows has no `resource`, nor the forkserver that needs it.
    import resource

__all__ = (
    "exec_in_forkserver",
    "forkserver_entry",
)

logger = logging.getLogger(__name__)


#
# Python-specialized execution paths
#


async def _recv_conn(conn):
    """Asynchronously receive one object from a multiprocessing connection."""
    await wait_for_readable_fd(conn.fileno())
    return conn.recv()


async def _wait_proc(proc):
    """Asynchronously wait for a multiprocessing process to exit, then reap it."""
    await wait_for_readable_fd(proc.sentinel)
    proc.join()


def _lost_child_outcome(exitcode: int | None) -> ChildOutcome:
    """Describe a forkserver child that died before sending its `ChildOutcome`.

    Whatever the child had written to stdout or stderr is lost with it:
    it is buffered in the child and only travels back over the pipe as part of the outcome.

    Parameters
    ----------
    exitcode
        The exit code of the joined child process,
        negative when it was terminated by a signal.

    Returns
    -------
    outcome
        A failed outcome explaining how the child died.
    """
    if exitcode is not None and exitcode < 0:
        try:
            reason = signal.Signals(-exitcode).name
        except ValueError:
            reason = f"signal {-exitcode}"
        returncode = exitcode
    else:
        # A child that exits without sending anything (e.g. a step calling `os._exit`)
        # can never get here with exitcode 0, but a failed run must not report success.
        reason = f"exit code {exitcode}"
        returncode = exitcode if exitcode else 1
    return ChildOutcome(
        returncode, "", f"The Python step died ({reason}) before sending back its result.\n"
    )


async def exec_in_forkserver(
    mp_ctx: multiprocessing.context.BaseContext,
    target,
    args: tuple,
    run: Run,
) -> ChildOutcome:
    """Run `target(*args, conn)` in a forkserver child and return the `ChildOutcome` it sends back.

    The child's pid is recorded on `run` so a running step can be interrupted.
    A child that dies without sending an outcome
    (typically because the build was aborted and it was killed with `SIGKILL`)
    yields a failed outcome describing how it died, just like a subprocess killed by a signal,
    instead of an `EOFError` escaping as an internal director error.
    """
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(target=target, args=(*args, child_conn))
    proc.start()
    child_conn.close()
    run.worker = ForkserverWorker(proc.pid, job_i=run.job_i)
    try:
        outcome = await _recv_conn(parent_conn)
    except (EOFError, OSError):
        # EOFError: the child died with the pipe still empty.
        # OSError (e.g. ConnectionResetError): it died halfway through sending.
        outcome = None
    finally:
        await _wait_proc(proc)
        parent_conn.close()
        run.worker = None
    return _lost_child_outcome(proc.exitcode) if outcome is None else outcome


@contextlib.contextmanager
def _redirect_os_fds(stdout_buf: io.StringIO, stderr_buf: io.StringIO):
    """Capture OS-level fd 1/2 output (subprocesses, C extensions)
    that a `sys.stdout` / `sys.stderr` `StringIO` redirect does not see.

    Redirects fds 1 and 2 to pipes drained on background threads
    for the duration of the `with` block,
    then restores the original fds
    and appends whatever the drain threads read to `stdout_buf` / `stderr_buf`.
    Restoring (rather than closing) the original fds is what signals EOF to the drain threads,
    which is what unblocks their join.
    """
    saved_out_fd = os.dup(1)
    saved_err_fd = os.dup(2)
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    os.dup2(w_out, 1)
    os.close(w_out)
    os.dup2(w_err, 2)
    os.close(w_err)

    def _read_fd_to_eof(fd: int) -> bytes:
        # Blocks until EOF, i.e. until every writer of this pipe end has closed it.
        # Same semantics as `communicate`:
        # a step that leaves a daemon holding the inherited fd open
        # will keep this thread (and the join that waits for it) waiting.
        with os.fdopen(fd, "rb") as f:
            return f.read()

    join_stdout = start_drain(functools.partial(_read_fd_to_eof, r_out))
    join_stderr = start_drain(functools.partial(_read_fd_to_eof, r_err))
    try:
        yield
    finally:
        os.dup2(saved_out_fd, 1)
        os.close(saved_out_fd)
        os.dup2(saved_err_fd, 2)
        os.close(saved_err_fd)
        # Decode tolerantly: subprocesses may emit invalid UTF-8.
        stdout_buf.write(decode_output(join_stdout()))
        stderr_buf.write(decode_output(join_stderr()))


def forkserver_entry(
    cmd: str,
    args: list[str],
    env_snapshot: dict[str, str],
    workdir: str,
    ep_value: str | None,
    result_conn,
) -> None:
    """Run a Python script or console_script entry point in a forkserver child.

    This function runs in a forked child process
    and sends a `ChildOutcome` back via `result_conn`.
    When `ep_value` is `None`, `cmd` is a Python script path run via `runpy.run_path`,
    with local imports auto-detected and registered as dynamic inputs.
    When `ep_value` is a `module:attr` string,
    the corresponding console_script function is imported and called directly
    without import tracking.
    """
    # Put the step in its own session; see `_signal_process_group`.
    # This cannot fail: a freshly forked child is never a process group leader.
    os.setsid()
    # Note that the time needed to start/stop the forkserver child is not counted,
    # which is a minor accepted discrepancy with the subprocess path.
    wtime_start = time.perf_counter()
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    returncode = 0
    ru_self_start = resource.getrusage(resource.RUSAGE_SELF)
    ru_children_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    # The inner try/except must run inside the `with` block,
    # so that the fd restore and append (in the `with` block's teardown)
    # happen after the traceback (if any) has already been written to `stderr_buf`, not before.
    with _redirect_os_fds(stdout_buf, stderr_buf):
        try:
            os.environ.clear()
            os.environ.update(env_snapshot)
            os.chdir(workdir)
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            sys.argv = [cmd, *args]
            try:
                if ep_value is None:
                    script_dir = str(Path(cmd).realpath().parent)
                    sys.path[0] = script_dir
                    runpy.run_path(cmd, run_name="__main__")
                else:
                    module_name, attr_name = ep_value.split(":", 1)
                    func = getattr(importlib.import_module(module_name), attr_name)
                    sys.exit(func())
            except SystemExit as exc:
                returncode = (
                    exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                )
            finally:
                # Run `atexit` handlers before sending the result,
                # so that any `amend()` calls from `atexit` handlers
                # are processed while the step is still RUNNING.
                # There is no public API for this in CPython;
                # `_run_exitfuncs` is a stable private implementation detail,
                # present in every CPython release from 2.0 to 3.14 (last checked 2026-08).
                with contextlib.suppress(AttributeError):
                    atexit._run_exitfuncs()
                if ep_value is None:
                    # This is the first call that connects to the director in this child.
                    # The connection must not be opened before the fork,
                    # which is why `get_rpc_client()` creates it lazily.
                    amend(inp=get_local_import_paths(script_path=Path(cmd)))
        except BaseException as exc:  # noqa: BLE001
            # All exceptions must be caught here,
            # to be able to send the corresponding output and return code
            # back to the director process.
            # Otherwise, the parent process would just see a connection error.
            # This path catches the exception itself, so `sys.excepthook` never runs
            # and the traceback shortening must be requested explicitly.
            print_step_traceback(exc, stderr_buf)
            returncode = 1
        finally:
            # Snapshot in a `finally`:
            # a step failing with an uncaught exception skips the tail of the `try` body,
            # which would otherwise leave the usage at zero
            # even though the step did consume CPU time.
            ru_self_end = resource.getrusage(resource.RUSAGE_SELF)
            ru_children_end = resource.getrusage(resource.RUSAGE_CHILDREN)
    wtime_end = time.perf_counter()
    usage = ResourceUsage.from_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end, wtime_start, wtime_end
    )
    result_conn.send(
        ChildOutcome(
            returncode,
            normalize_newlines(stdout_buf.getvalue()),
            normalize_newlines(stderr_buf.getvalue()),
            usage,
        )
    )
