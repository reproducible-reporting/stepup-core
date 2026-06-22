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
"""In-process execution of steps.

The `StepExecutor` runs each step directly inside the director's event loop as an asyncio task.
Commands run as asyncio subprocesses (shell / direct exec) or in a forkserver child (Python
scripts and console-script entry points).

File hashing is delegated to a separate process via `stepup.core.hasher`.

A single `StepExecutor` instance serves all concurrent steps.
A per-step mutable state lives in a `RunningStep` created for each job.
"""

import asyncio
import atexit
import contextlib
import functools
import importlib
import io
import logging
import os
import pickle
import resource
import runpy
import shlex
import shutil
import subprocess
import sys
import threading
import traceback
from collections.abc import AsyncGenerator
from importlib.metadata import entry_points
from time import perf_counter

import attrs
from path import Path

from .asyncio import await_fd_readable
from .enums import FileState, StepState
from .exceptions import RPCError
from .hash import FileHash, StepHash, compare_step_hashes
from .hasher import HashResult, HashTask, hash_fork_entry
from .reporter import ReporterClient
from .scheduler import Scheduler
from .step import Step, split_step_label
from .utils import DBLock, get_local_import_paths
from .workflow import Workflow

__all__ = ("RunningStep", "StepExecutor")


logger = logging.getLogger(__name__)


#
# Forkserver child entry point for Python step execution
#


PYCODE_WRAPPER = """\
import os
import sys
import runpy
from path import Path
from stepup.core.api import amend
from stepup.core.utils import get_local_import_paths
sys.argv = {argv}
try:
    runpy.run_path({script}, run_name="__main__")
finally:
    amend(inp=get_local_import_paths(script_path=Path({script})))
"""


def _forkserver_entry(
    cmd: str,
    args: list[str],
    env_snapshot: dict[str, str],
    workdir: str,
    ep_value: str | None,
    result_conn,
) -> None:
    """Entry point for forkserver-launched Python executions.

    This function runs in a forked child process and sends results back via `result_conn`.
    When `ep_value` is `None`, `cmd` is a Python script path run via `runpy.run_path`,
    with local imports auto-detected and amended as inputs.
    When `ep_value` is a `module:attr` string, the corresponding console_script function
    is imported and called directly without import tracking.
    """
    try:
        os.environ.clear()
        os.environ.update(env_snapshot)
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        returncode = 0
        ru_self_start = resource.getrusage(resource.RUSAGE_SELF)
        ru_children_start = resource.getrusage(resource.RUSAGE_CHILDREN)
        ru_self_end = ru_self_start
        ru_children_end = ru_children_start
        os.chdir(workdir)
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        sys.argv = [cmd, *args]
        try:
            if ep_value is None:
                runpy.run_path(cmd, run_name="__main__")
            else:
                module_name, attr_name = ep_value.split(":", 1)
                func = getattr(importlib.import_module(module_name), attr_name)
                func()
        except SystemExit as exc:
            returncode = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        finally:
            # Run atexit handlers before sending the result so that any amend() calls
            # from atexit handlers are processed while the step is still RUNNING.
            # There is no public API for this in CPython; _run_exitfuncs is a stable
            # private implementation detail present in every CPython release since 2.0.
            with contextlib.suppress(AttributeError):
                atexit._run_exitfuncs()
            if ep_value is None:
                # Must be imported ONLY in the forked process:
                # it opens a new connection to the director socket,
                # which should happen in the forked process, not its parent.
                from stepup.core.api import amend  # noqa: PLC0415

                amend(inp=get_local_import_paths(script_path=Path(cmd)))
        ru_self_end = resource.getrusage(resource.RUSAGE_SELF)
        ru_children_end = resource.getrusage(resource.RUSAGE_CHILDREN)
    except BaseException:  # noqa: BLE001
        # All exceptions must be caught here, to be able to send the corresponding
        # output and return code back to the director process.
        # Otherwise, the parent process would just see a connection error.
        traceback.print_exc(file=stderr_buf)
        returncode = 1
    utime = (ru_self_end.ru_utime - ru_self_start.ru_utime) + (
        ru_children_end.ru_utime - ru_children_start.ru_utime
    )
    stime = (ru_self_end.ru_stime - ru_self_start.ru_stime) + (
        ru_children_end.ru_stime - ru_children_start.ru_stime
    )
    result_conn.send((returncode, stdout_buf.getvalue(), stderr_buf.getvalue(), utime, stime))


def _executable_uses_same_python(path_exec: str) -> bool:
    """Check if an executable script's shebang resolves to the same Python as the current process.

    This detects console_script wrappers installed in PATH-extended locations
    (e.g., additional environment modules loaded on top of the base Python module)
    whose shebang points to the same Python executable as `sys._base_executable`.
    When true, the executable and the forkserver use the same interpreter and inherit
    the same environment variables, so their behavior is equivalent.

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
    base_exec = Path(sys._base_executable).resolve()
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
        shebang_python = Path(python_on_path).resolve()
    else:
        shebang_python = Path(parts[0]).resolve()
    return shebang_python == base_exec


@functools.cache
def _detect_python_entrypoint(cmd: str) -> str | None:
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

    Raises
    ------
    ValueError
        When `cmd` is registered as a console_script but cannot be found on `PATH`,
        which indicates a broken installation.
    """
    eps = list(entry_points(group="console_scripts", name=cmd))
    if not eps:
        return None
    ep_value = eps[0].value
    which_path = shutil.which(cmd)
    if which_path is None:
        raise ValueError(
            f"Command '{cmd}' is registered as a Python console_script entry point "
            "but was not found on PATH. The installation may be broken."
        )
    # Verify the executable is compatible with the current Python environment.
    # Fast path: binary lives inside the current env or its parent (the env the venv was
    # created from). Slow path: binary's shebang resolves to the same Python interpreter,
    # covering packages installed via PYTHONPATH-extending environment modules.
    resolved = Path(which_path).realpath()
    env_bins = {(Path(sys.prefix) / "bin").realpath(), (Path(sys.exec_prefix) / "bin").realpath()}
    if sys.prefix != sys.base_prefix:
        env_bins.add((Path(sys.base_prefix) / "bin").realpath())
        env_bins.add((Path(sys.base_exec_prefix) / "bin").realpath())
    path_ok = any(resolved.startswith(d + "/") or resolved == d for d in env_bins)
    if not path_ok and not _executable_uses_same_python(which_path):
        print(
            f"WARNING: Command '{cmd}' is a Python entry point but its executable"
            f" ('{which_path}') is not in the current Python environment ({sys.prefix})."
            " Falling back to direct subprocess execution.",
            file=sys.stderr,
        )
        return None
    return ep_value


def check_executable(executable: Path, shebang: str | None = None) -> str | None:
    """Check if the executable looks fine.

    Parameters
    ----------
    executable
        The (working-directory-resolved) path to the executable to check.
    shebang
        The expected shebang line, if any.

    Returns
    -------
    message
        `None` when the executable is acceptable, otherwise a human-readable error message.
    """
    # See https://en.wikipedia.org/wiki/Shebang_%28Unix%29
    if not executable.is_file():
        # The executable is probably in the PATH,
        # i.e. not a custom script, so not checking
        return None
    # Check if the file is executable
    if not executable.access(os.X_OK):
        # This is not a script, so not checking the shebang.
        return f"File is not executable: {executable}"
    # Check if the file is binary.
    # https://stackoverflow.com/a/7392391
    with open(executable, "rb") as fh:
        head = fh.read(1024)
    printable_text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    # Check if the file is binary by translating non-text characters
    if bool(head.translate(None, printable_text_chars)):
        # This is unlikely to be a script, so not checking the shebang.
        return None
    if shebang is None:
        if head[:3] != b"#!/":
            return f"Script does not start with a shebang: {executable}"
    elif not head.startswith(shebang.encode("utf-8")):
        return f"Script does not start with the expected shebang ({shebang}): {executable}"
    return None


#
# Async process helpers
#


async def _recv_conn(conn):
    """Asynchronously receive one object from a multiprocessing connection."""
    await await_fd_readable(conn.fileno())
    return conn.recv()


async def _wait_proc(proc):
    """Asynchronously wait for a multiprocessing process to exit, then reap it."""
    await await_fd_readable(proc.sentinel)
    proc.join()


async def run_in_forkserver(mp_ctx, target, args: tuple, rs: "RunningStep | None" = None):
    """Run `target(*args, conn)` in a forkserver child and return the object it sends back.

    The child's pid is recorded on `rs` (when given) so a running step can be interrupted.
    """
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(target=target, args=(*args, child_conn))
    proc.start()
    child_conn.close()
    if rs is not None:
        rs.pid = proc.pid
    try:
        result = await _recv_conn(parent_conn)
    finally:
        await _wait_proc(proc)
        parent_conn.close()
        if rs is not None:
            rs.pid = None
    if isinstance(result, BaseException):
        raise result
    return result


def _communicate_wait4(
    proc: subprocess.Popen, stdin_data: bytes | None
) -> tuple[bytes, bytes, float, float]:
    """Communicate with `proc` and return (stdout, stderr, utime, stime).

    Reads stdout and stderr concurrently in threads to avoid pipe-full deadlock,
    then calls `os.wait4` to reap the child and capture its individual CPU usage.
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _read(pipe, chunks):
        chunks.append(pipe.read())

    threads = []
    for pipe, chunks in ((proc.stdout, stdout_chunks), (proc.stderr, stderr_chunks)):
        if pipe is not None:
            t = threading.Thread(target=_read, args=(pipe, chunks), daemon=True)
            t.start()
            threads.append(t)

    if stdin_data is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_data)
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()

    for t in threads:
        t.join()

    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return b"".join(stdout_chunks), b"".join(stderr_chunks), rusage.ru_utime, rusage.ru_stime


async def _spawn_subprocess(
    cmd, *, shell: bool, env: dict, cwd: Path, stdin_data: bytes | None, rs: "RunningStep | None"
) -> tuple[int, bytes, bytes, float, float]:
    """Run `cmd` as a subprocess and return (returncode, stdout, stderr, utime, stime).

    The process is created synchronously so that `rs.proc` can be set immediately for interrupts.
    Blocking I/O and the `os.wait4` reap are offloaded to a thread-pool executor
    so the event loop stays responsive.

    Using `subprocess.Popen` (rather than `asyncio.create_subprocess_*`)
    means asyncio's child watcher never registers this PID,
    so our `os.wait4` call captures per-process CPU time without racing against the watcher.
    """
    stdin = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
    proc = subprocess.Popen(
        cmd,
        shell=shell,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if rs is not None:
        rs.proc = proc
    try:
        loop = asyncio.get_running_loop()
        stdout, stderr, utime, stime = await loop.run_in_executor(
            None, _communicate_wait4, proc, stdin_data
        )
    finally:
        if rs is not None:
            rs.proc = None
    return proc.returncode, stdout, stderr, utime, stime


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "ignore")


async def _run_subprocess(
    cmd: str | list[str],
    *,
    shell: bool,
    env: dict,
    cwd: Path,
    rs: "RunningStep",
    stdin_data: bytes | None = None,
) -> tuple[int, str, str, float, float]:
    """Run `cmd` as a subprocess with or without a shell.

    Returns `(rc, stdout, stderr, utime, stime)`.
    """
    first_arg = shlex.split(cmd)[0] if isinstance(cmd, str) else cmd[0]
    cmd_str = cmd if isinstance(cmd, str) else shlex.join(cmd)
    message = check_executable(cwd / Path(first_arg))
    if message is not None:
        return 1, "", message + "\n", 0.0, 0.0
    rc, out, err, utime, stime = await _spawn_subprocess(
        cmd, shell=shell, env=env, cwd=cwd, stdin_data=stdin_data, rs=rs
    )
    err = _decode(err)
    if rc != 0:
        err += f"Command failed with return code {rc}: {cmd_str}\n"
    return rc, _decode(out), err, utime, stime


async def _runpy(
    script: str, args: list[str], env: dict, cwd: Path, mp_ctx, rs: "RunningStep"
) -> tuple[int, str, str, float, float]:
    """Run a Python script, amending its local imports as inputs."""
    message = check_executable(cwd / Path(script), shebang="#!/usr/bin/env python3")
    if message is not None:
        return 1, "", message + "\n", 0.0, 0.0
    if mp_ctx is not None:
        return await run_in_forkserver(
            mp_ctx, _forkserver_entry, (script, args, env, str(cwd), None), rs
        )
    wrapper = PYCODE_WRAPPER.format(argv=repr([script, *args]), script=repr(script))
    return await _run_subprocess(
        [sys.executable, "-"], shell=False, env=env, cwd=cwd, rs=rs, stdin_data=wrapper.encode()
    )


async def _runpyep(
    cmd: str, args: list[str], ep_value: str, env: dict, cwd: Path, mp_ctx, rs: "RunningStep"
) -> tuple[int, str, str, float, float]:
    """Run a Python console_script entry point, using the forkserver when available."""
    if mp_ctx is not None:
        return await run_in_forkserver(
            mp_ctx, _forkserver_entry, (cmd, args, env, str(cwd), ep_value), rs
        )
    return await _run_subprocess([cmd, *args], shell=False, env=env, cwd=cwd, rs=rs)


#
# Per-step state
#


@attrs.define(eq=False)
class RunningStep:
    """Mutable state for a single step while it is being processed.

    Instances use identity-based equality and hashing so they can be tracked in a set
    of currently running steps.
    """

    i: int = attrs.field()
    """The index from the node table for the step, used to set `STEPUP_STEP_I`."""

    command: str = attrs.field()
    """The raw command to be executed for the step."""

    subshell: bool = attrs.field()
    """Whether the command is executed via a subshell."""

    workdir: Path = attrs.field()
    """The working directory where the command will be executed."""

    stdout: str = attrs.field(init=False, default="")
    """The standard output captured from the command execution."""

    stderr: str = attrs.field(init=False, default="")
    """The standard error captured from the command execution."""

    perf_info: str = attrs.field(init=False, default="")
    """Performance information collected during the command execution."""

    inp_messages: list = attrs.field(init=False, factory=list)
    """Messages related to input validation issues: unexpected changes and deleted inputs."""

    inp_digest: bytes = attrs.field(init=False, default=b"")
    """The input digest, which can be useful for some steps."""

    out_missing: list = attrs.field(init=False, factory=list)
    """List of expected output files that were not created."""

    rescheduled_info: str = attrs.field(init=False, default="")
    """Information about why the step was rescheduled."""

    returncode: int | None = attrs.field(init=False, default=None)
    """The return code from the command."""

    success: bool = attrs.field(init=False, default=True)
    """Flag indicating whether the step was handled successfully."""

    proc: asyncio.subprocess.Process | subprocess.Popen | None = attrs.field(
        init=False, default=None
    )
    """The subprocess currently running the command, if any."""

    pid: int | None = attrs.field(init=False, default=None)
    """The pid of the forkserver child currently running the command, if any."""

    @property
    def description(self):
        """The command, optionally annotated with the working directory."""
        return (
            self.command if self.workdir == Path("./") else f"{self.command}  # wd={self.workdir}"
        )

    def merge_hash_result(self, result: "HashResult") -> None:
        """Apply the fields of a `HashResult` onto this running step.

        List fields are extended; scalar fields are only written when the result provides a
        non-default value, so a prior `compute_inp_step_hash` result is not clobbered by a
        subsequent `compute_out_step_hash` call.
        """
        if result.inp_messages:
            self.inp_messages = result.inp_messages
        self.out_missing.extend(result.out_missing)
        if not result.success:
            self.success = False
        if result.inp_digest:
            self.inp_digest = result.inp_digest


@attrs.define
class StepExecutor:
    """Run steps in the director process as asyncio tasks.

    One shared instance serves all concurrent steps. The methods `validate_amended_job`,
    `try_skip_job` and `execute_job` are the coroutines created by the builder for each job.
    """

    scheduler: Scheduler = attrs.field()
    """The scheduler that is managing the jobs."""

    workflow: Workflow = attrs.field()
    """The workflow that the executor is interacting with."""

    dblock: DBLock = attrs.field()
    """Lock for workflow database access."""

    reporter: ReporterClient = attrs.field()
    """A reporter to send progress and terminal output to."""

    show_perf: bool = attrs.field()
    """Flag to enable detailed CPU usage of each step."""

    explain_rerun: bool = attrs.field()
    """Flag to explain why a step could not be skipped."""

    mp_ctx: object = attrs.field(kw_only=True, default=None)
    """Forkserver multiprocessing context, or None to use plain subprocesses."""

    step_env: dict = attrs.field(kw_only=True, factory=dict)
    """Environment variables exported to step child processes, overriding `os.environ`."""

    running: set = attrs.field(init=False, factory=set)
    """The set of `RunningStep` instances whose command is currently running."""

    @property
    def child_env(self) -> dict:
        """The base environment for step child processes: `os.environ` with `step_env` applied."""
        return {**os.environ, **self.step_env}

    #
    # Functions called by jobs
    #

    async def _reset_step_to_pending(self, step: Step) -> None:
        """Discard a step's stored hash and transition it back to PENDING for re-execution."""
        async with self.dblock:
            step.clean_before_run()
            step.delete_hash()
            step.set_state(StepState.PENDING)

    async def validate_amended_job(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        step_hash: StepHash,
    ):
        """Test if the inputs (hashes) have changed, which would invalidate the amended step info.

        If the job can be validated, it is put back in the pending state,
        so that it can be re-queued when new inputs arrive.
        """
        async with self.new_step(step, inp_hashes, env_vars, check_hash=False) as (rs, new_hash):
            if not (new_hash is None or step_hash.inp_digest == new_hash.inp_digest):
                await self.outdated_amended(rs, step_hash, new_hash)
                # Inputs have changed, so discard amended info
                await self._reset_step_to_pending(step)
                return

            # If we get here:
            # - No inputs have changed, or.
            # - Failed to create the new step due to unexpected input changes.
            # In both cases, the step is not invalidated and just made pending again.
            async with self.dblock:
                step.set_state(StepState.PENDING)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)

    async def try_skip_job(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        step_hash: StepHash,
    ):
        """Try skipping a step."""
        async with self.new_step(step, inp_hashes, env_vars) as (rs, new_hash):
            if new_hash is None:
                # Failed to create the new step due to unexpected input changes.
                return

            if step_hash.inp_digest != new_hash.inp_digest:
                # The inputs have changed, so must run.
                await self.noskip(rs, step_hash, new_hash)
                await self._reset_step_to_pending(step)
                return

            # Compute the output part of the step hash.
            new_hash, new_out_hashes = await self.compute_out_step_hash(step, rs, new_hash)

            if step_hash.out_digest != new_hash.out_digest:
                # The outputs have changed, so must run.
                await self.noskip(rs, step_hash, new_hash)
                await self._reset_step_to_pending(step)
                return

            # All checks passed: no need to run the step, just simulate the products.
            await self.skip(rs, step_hash)
            async with self.dblock:
                self.workflow.update_file_hashes(new_out_hashes, "succeeded")
                step.completed(new_hash)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)

    async def execute_job(
        self, step: Step, inp_hashes: list[tuple[str, FileHash]], env_vars: list[str]
    ):
        """Execute a step (no skipping)."""
        async with self.new_step(step, inp_hashes, env_vars) as (rs, new_hash):
            if new_hash is None:
                # Failed to create the new step due to unexpected input changes.
                return

            # Run the step
            async with self.dblock:
                step.clean_before_run()
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.run(rs)

            # Recompute the step hash (inputs and outputs).
            # Hashes are always updated, even for failed commands,
            # so outputs can be removed safely if they are no longer needed.
            new_hash, new_inp_hashes, new_out_hashes = await self.compute_full_step_hash(step, rs)

            success = rs.success

            async with self.dblock:
                rescheduled_info = step.get_rescheduled_info()
                if rescheduled_info != "":
                    self.rescheduled(rs, rescheduled_info)
                    success = False
                self.workflow.update_file_hashes(
                    new_out_hashes, "succeeded" if success else "failed"
                )
                if not success:
                    new_hash = None
                step.completed(new_hash)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)

            # Report the result of running the step
            await self.report(rs)

            if len(new_inp_hashes) > 0:
                # Changes to inputs are suspect and can break everything.
                # End the build phase gracefully by putting the scheduler on hold.
                self.scheduler.on_hold = True
                await self.reporter(
                    "ERROR", "The scheduler has been put on hold due to unexpected input changes."
                )

    #
    # Step lifecycle helpers
    #

    @contextlib.asynccontextmanager
    async def new_step(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        *,
        check_hash: bool = True,
    ) -> AsyncGenerator[tuple[RunningStep, StepHash | None], None]:
        """Set up a fresh `RunningStep` and compute the input part of its step hash.

        Yields
        ------
        rs
            The per-step state object.
        new_step_hash
            The new hash of the step, with the input part already computed, if available.
            `None` if, unexpectedly, some inputs are missing or have changed.
        """
        command, workdir = split_step_label(step.label)
        rs = RunningStep(step.i, command, step.get_subshell(), workdir)
        new_step_hash = await self.compute_inp_step_hash(rs, inp_hashes, env_vars, check_hash)
        if new_step_hash is None and check_hash:
            # The hashes of the input files on disk differ from those in the database,
            # or some inputs were deleted. This breaks the workflow, so flag the step as failed.
            async with self.dblock:
                step.completed(None)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.report(rs)
            self.scheduler.on_hold = True
            await self.reporter(
                "ERROR", "The scheduler has been put on hold due to unexpected input changes."
            )
            yield rs, None
        else:
            yield rs, new_step_hash

    async def _run_hash(self, task: HashTask) -> HashResult:
        """Compute (part of) a step hash in a forkserver child or a subprocess."""
        if self.mp_ctx is not None:
            return await run_in_forkserver(self.mp_ctx, hash_fork_entry, (task,))
        returncode, stdout, stderr, _, _ = await _spawn_subprocess(
            [sys.executable, "-c", "from stepup.core.hasher import hasher_tool; hasher_tool()"],
            shell=False,
            env=self.child_env,
            cwd=Path.cwd(),
            stdin_data=pickle.dumps(task),
            rs=None,
        )
        if returncode != 0:
            raise RPCError(f"The hashes tool failed: {_decode(stderr)}")
        return pickle.loads(stdout)

    async def compute_inp_step_hash(
        self,
        rs: RunningStep,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        check_hash: bool = True,
    ) -> StepHash | None:
        """Compute the input part of a step hash and apply it to `rs`."""
        child_env = self.child_env
        task = HashTask(
            mode="inp",
            step_key=rs.description,
            extended=self.explain_rerun,
            subshell=rs.subshell,
            check_hash=check_hash,
            inp_hashes=inp_hashes,
            env_var_values=[(name, child_env.get(name)) for name in env_vars],
        )
        result = await self._run_hash(task)
        rs.merge_hash_result(result)
        return result.step_hash

    async def compute_out_step_hash(
        self, step: Step, rs: RunningStep, step_hash: StepHash
    ) -> tuple[StepHash, list[tuple[str, FileHash]]]:
        """Compute the output part of a step hash and apply it to `rs`."""
        async with self.dblock:
            out_hashes = list(step.out_paths(yield_hash=True))
        task = HashTask(mode="out", step_hash=step_hash, out_hashes=out_hashes)
        result = await self._run_hash(task)
        rs.merge_hash_result(result)
        return result.step_hash, result.new_out_hashes

    async def compute_full_step_hash(
        self, step: Step, rs: RunningStep
    ) -> tuple[StepHash, list[tuple[str, FileHash]], list[tuple[str, FileHash]]]:
        """Compute a new step hash with updated input and output file hashes, applied to `rs`."""
        async with self.dblock:
            # Some inputs may be amended and still unavailable,
            # for which checking hashes is too early.
            # Therefore, only check the hashes of built and static files.
            inp_hashes = [
                (path, file_hash)
                for path, file_state, file_hash in step.inp_paths(yield_state=True, yield_hash=True)
                if file_state in (FileState.BUILT, FileState.STATIC)
            ]
            env_vars = list(step.env_vars())
            out_hashes = list(step.out_paths(yield_hash=True))
        child_env = self.child_env
        task = HashTask(
            mode="full",
            step_key=rs.description,
            extended=self.explain_rerun,
            subshell=rs.subshell,
            check_hash=True,
            inp_hashes=inp_hashes,
            env_var_values=[(name, child_env.get(name)) for name in env_vars],
            out_hashes=out_hashes,
        )
        result = await self._run_hash(task)
        rs.merge_hash_result(result)
        return result.step_hash, result.new_inp_hashes, result.new_out_hashes

    async def run(self, rs: RunningStep):
        """Run the command of the step described by `rs`."""
        await self.reporter("START", rs.description)
        await self.reporter.start_step(rs.description, rs.i)

        env = self.child_env
        # For internal use in command:
        env["STEPUP_STEP_I"] = str(rs.i)
        # Client code may use the following:
        env["STEPUP_STEP_INP_DIGEST"] = rs.inp_digest.hex()
        env["ROOT"] = str(Path.cwd().relpath(rs.workdir))
        env["HERE"] = str(rs.workdir.relpath())
        # Note: the variables defined here should be listed in stepup.core.api.getenv

        if self.show_perf:
            pt_initial = perf_counter()

        self.running.add(rs)
        utime = 0.0
        stime = 0.0
        try:
            parts = shlex.split(rs.command)
            if not parts:
                raise ValueError(f"Empty command: {rs.command!r}")
            if rs.subshell:
                returncode, stdout, stderr, utime, stime = await _run_subprocess(
                    rs.command, shell=True, env=env, cwd=rs.workdir, rs=rs
                )
            elif parts[0].endswith(".py"):
                returncode, stdout, stderr, utime, stime = await _runpy(
                    parts[0], parts[1:], env, rs.workdir, self.mp_ctx, rs
                )
            else:
                ep_value = _detect_python_entrypoint(parts[0])
                if ep_value is not None:
                    returncode, stdout, stderr, utime, stime = await _runpyep(
                        parts[0], parts[1:], ep_value, env, rs.workdir, self.mp_ctx, rs
                    )
                else:
                    returncode, stdout, stderr, utime, stime = await _run_subprocess(
                        parts, shell=False, env=env, cwd=rs.workdir, rs=rs
                    )
        except BaseException as exc:  # noqa: BLE001
            returncode = exc.code if isinstance(exc, SystemExit) else 1
            stdout = ""
            stderr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        finally:
            self.running.discard(rs)

        rs.returncode = returncode
        rs.stdout = stdout
        rs.stderr = stderr

        if self.show_perf:
            wtime = perf_counter() - pt_initial
            ru_lines = [
                f"User CPU time [s]:   {utime:9.4f}",
                f"System CPU time [s]: {stime:9.4f}",
                f"Total CPU time [s]:  {utime + stime:9.4f}",
                f"Wall time [s]:       {wtime:9.4f}",
            ]
            rs.perf_info = "\n".join(ru_lines)
        if rs.returncode != 0:
            rs.success = False

    def interrupt(self, sig: int):
        """Send a signal to all currently running step commands."""
        for rs in list(self.running):
            proc = rs.proc
            pid = rs.pid
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    logger.info("Interrupting subprocess %r with signal %d", proc.pid, sig)
                    proc.send_signal(sig)
            elif pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    logger.info("Interrupting forkserver child %d with signal %d", pid, sig)
                    os.kill(pid, sig)

    def rescheduled(self, rs: RunningStep, rescheduled_info: str):
        rs.rescheduled_info = rescheduled_info
        # Erase other error info to keep the screen output concise.
        rs.success = True
        rs.stderr = ""

    async def report(self, rs: RunningStep):
        pages = []
        if not rs.success:
            # Format command for display (can be copied and pasted into a shell).
            display_cmd = rs.command
            if rs.workdir != Path("./"):
                display_cmd = f"(cd {rs.workdir} && ({display_cmd}))"
            lines = [f"Command               {display_cmd}"]
            if rs.returncode is not None:
                lines.append(f"Return code           {rs.returncode}")
            pages.append(("Step info", "\n".join(lines)))
        if len(rs.perf_info) > 0:
            pages.append(("Performance details", rs.perf_info))
        if rs.rescheduled_info != "":
            pages.append(("Rescheduling due to unavailable amended inputs", rs.rescheduled_info))
        else:
            if len(rs.inp_messages) > 0:
                rs.inp_messages.sort()
                pages.append(("Invalid inputs", "\n".join(rs.inp_messages)))
            if len(rs.out_missing) > 0:
                rs.out_missing.sort()
                pages.append(("Expected outputs not created", "\n".join(rs.out_missing)))
        stdout = rs.stdout.rstrip()
        if len(stdout) > 0:
            pages.append(("Standard output", stdout))
        stderr = rs.stderr.rstrip()
        if len(stderr) > 0:
            pages.append(("Standard error", stderr))
        if rs.rescheduled_info != "":
            action = "RESCHEDULE"
        elif rs.success:
            action = "SUCCESS"
        else:
            action = "FAIL"
        await self.reporter.stop_step(rs.i)
        await self.reporter(action, rs.description, pages)

    async def skip(self, rs: RunningStep, step_hash: StepHash):
        pages = []
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(step_hash, step_hash)
            if len(page_change) > 0:
                raise AssertionError(
                    "A skipped step cannot have changes in inputs, env vars or outputs."
                )
            if len(page_same) > 0:
                pages.append(("No changes observed", page_same))
        await self.reporter("SKIP", rs.description, pages)

    async def noskip(self, rs: RunningStep, old_hash: StepHash, new_hash: StepHash):
        if self.explain_rerun:
            pages = []
            if len(rs.out_missing) > 0:
                pages.append(("Missing output files", "\n".join(rs.out_missing)))
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            if len(page_change) > 0:
                pages.append(("Changes causing rerun", page_change))
            if len(page_same) > 0:
                pages.append(("Remained the same", page_same))
            await self.reporter("NOSKIP", rs.description, pages)

    async def outdated_amended(self, rs: RunningStep, old_hash: StepHash, new_hash: StepHash):
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            pages = [("Outdated amended step information", page_change)]
            if len(page_same) > 0:
                pages.append(("Remained the same (or missing)", page_same))
            await self.reporter("DROPAMEND", rs.description, pages)
