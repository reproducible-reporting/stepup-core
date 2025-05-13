# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""Worker process."""

import argparse
import asyncio
import contextlib
import ctypes
import inspect
import io
import os
import queue
import resource
import shlex
import subprocess
import sys
import threading
import traceback
from collections.abc import AsyncGenerator
from importlib.metadata import entry_points
from time import perf_counter

import attrs
from path import Path

from .asyncio import wait_for_path
from .enums import FileState, StepState
from .exceptions import RPCError
from .hash import FileHash, StepHash, compare_step_hashes, fmt_file_hash_diff
from .reporter import ReporterClient
from .rpc import AsyncRPCClient, allow_rpc, serve_socket_rpc
from .scheduler import Scheduler
from .step import Step, split_step_label
from .utils import DBLock
from .workflow import Workflow

__all__ = ("WorkThread", "WorkerClient", "WorkerHandler", "WorkerStep")


#
# In the main director process
#


@attrs.define
class WorkerClient:
    """Client interface to a worker, used by the director process."""

    scheduler: Scheduler = attrs.field()
    """The scheduler that sends jobs (via the runner) to this worker (and others)."""

    workflow: Workflow = attrs.field()
    """The workflow that the client is interacting with."""

    dblock: DBLock = attrs.field()
    """Lock for workflow database access."""

    reporter: ReporterClient = attrs.field()
    """A reporter to send progress and terminal output to."""

    director_socket_path: Path = attrs.field()
    """The path of the director socket.

    A step being executed by a worker needs this socket extend the workflow.
    """

    show_perf: bool = attrs.field()
    """Flag to enable detailed CPU usage of each step."""

    explain_rerun: bool = attrs.field()
    """Flag to explain why a step could not be skipped."""

    idx: int = attrs.field(converter=int)
    """The integer index of the worker (in the list of workers kept by the Runner instance)."""

    process: asyncio.subprocess.Process | None = attrs.field(init=False, default=None)
    """The worker process that is running the steps."""

    client: AsyncRPCClient | None = attrs.field(init=False, default=None)
    """The RPC client to communicate with the worker process."""

    #
    # Setup and teardown
    #

    async def boot(self, log: io.TextIOBase, stop_event: asyncio.Event):
        worker_socket_path = self.director_socket_path.parent / f"worker{self.idx}"
        argv = [
            sys.executable,
            "-m",
            "stepup.core.worker",
            self.director_socket_path,
            worker_socket_path,
            str(self.idx),
        ]
        if self.show_perf:
            argv.append("--show-perf")
        if self.explain_rerun:
            argv.append("--explain-rerun")
        if self.reporter.socket_path is not None:
            argv.append(f"--reporter={self.reporter.socket_path}")
        # Create the worker process
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        # Wait for the socket to appear and connect to it
        await wait_for_path(worker_socket_path, stop_event)
        self.client = await AsyncRPCClient.socket(worker_socket_path)

    async def close(self):
        await self.client.close()
        await self.process.wait()
        self.process = None

    #
    # Functions called by jobs
    #

    async def validate_amended_job(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        step_hash: StepHash,
    ) -> bool:
        """Test if the inputs (hashes) have changed, which would invalidate the amended step info.

        Parameters
        ----------
        step
            The step whose amended inputs need validation.
        inp_hashes
            List of tuples of input paths and their file hashes.
        env_vars
            List of environment variable names used by the step.
        step_hash
            The hash of the step to validate.

        Returns
        -------
        must_run
            True if the amended inputs are invalid.
            This can be fixed by running the step again.
        """
        async with self.new_step(step, inp_hashes, env_vars, check_hash=False) as new_step_hash:
            if not (new_step_hash is None or step_hash.inp_digest == new_step_hash.inp_digest):
                await self.client.call.outdated_amended(step_hash, new_step_hash)
                # Inputs have changed, so must run.
                async with self.dblock:
                    step.clean_before_run()
                    step.delete_hash()
                return True

            # We may reach this code for two reasons:
            # - No inputs have changed.
            # - Failed to create the new step due to unexpected input changes.
            #   This may happen when validating, because inputs may still be coming in.
            # In both cases, the step is not invalidated and just sent back to the scheduler.
            async with self.dblock:
                step.set_state(StepState.PENDING)
                step.queue_if_appropriate()
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.client.call.unset_step()
            return False

    async def try_skip_job(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        step_hash: StepHash,
    ) -> bool:
        """Try skipping a step.

        Parameters
        ----------
        step
            The step to skip (if outputs are up to date).
        inp_hashes
            List of tuples of input paths and their file hashes.
        env_vars
            List of environment variable names used by the step.
        step_hash
            The hash of the step to skip.

        Returns
        -------
        must_run
            True if skipping failed and the steps needs to be rescheduled for running immediately.
        """
        async with self.new_step(step, inp_hashes, env_vars) as new_step_hash:
            if new_step_hash is None:
                # Failed to create the new step due to unexpected input changes.
                return False

            if step_hash.inp_digest != new_step_hash.inp_digest:
                # The inputs have changed, so must run.
                await self.client.call.noskip(step_hash, new_step_hash)
                async with self.dblock:
                    step.clean_before_run()
                    step.delete_hash()
                return True

            # Delegate the calculation of the output part of the step hash to the worker.
            # With skipping=True, the worker knows the outputs should not have changed
            # and will report it on screen.
            new_step_hash, new_out_hashes = await self.compute_out_step_hash(step, new_step_hash)

            if step_hash.out_digest != new_step_hash.out_digest:
                # The inputs or outputs have changed, so must run.
                await self.client.call.noskip(step_hash, new_step_hash)
                async with self.dblock:
                    step.clean_before_run()
                    step.delete_hash()
                return True

            # All checks passed.
            # No need to run the step, just simulate the creation of the products.
            await self.client.call.skip(step_hash)
            async with self.dblock:
                self.workflow.update_file_hashes(new_out_hashes, "succeeded")
                step.completed(new_step_hash)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            return False

    async def execute_job(
        self, step: Step, inp_hashes: list[tuple[str, FileHash]], env_vars: list[str]
    ):
        """Execute a step (no skipping).

        Parameters
        ----------
        step
            The step to run.
        inp_hashes
            List of tuples of input paths and their file hashes.
        env_vars
            List of environment variable names used by the step.
        """
        async with self.new_step(step, inp_hashes, env_vars) as new_step_hash:
            if new_step_hash is None:
                # Failed to create the new step due to unexpected input changes.
                return

            # Run the step
            async with self.dblock:
                step.set_state(StepState.RUNNING)
                step.clean_before_run()
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.client.call.run()

            # Check the result:
            # - Hashes are always updated, even for failed commands.
            #   This way, outputs can be removed safely if they are not needed anymore.
            # - The input part of the Step Hash is recomputed.
            new_step_hash, new_inp_hashes, new_out_hashes = await self.compute_full_step_hash(step)

            # Check if the step was successful, including check of inputs and outputs.
            success = await self.client.call.get_success()

            # Update the workflow with the new hashes and the completion of the step.
            async with self.dblock:
                rescheduled_info = step.get_rescheduled_info()
                if rescheduled_info != "":
                    await self.client.call.rescheduled(rescheduled_info)
                    success = False
                self.workflow.update_file_hashes(
                    new_out_hashes, "succeeded" if success else "failed"
                )
                if not success:
                    new_step_hash = None
                step.completed(new_step_hash)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)

            # Report the result of running the step
            await self.client.call.report()

            if len(new_inp_hashes) > 0:
                # Changes to inputs are suspect and can break everything.
                # Therefore, the run phase is ended gracefully by draining the scheduler.
                self.scheduler.drain()
                await self.reporter(
                    "ERROR", "The scheduler has been drained due to unexpected input changes."
                )

    #
    # Wrappers around RPCs
    #

    @contextlib.asynccontextmanager
    async def new_step(
        self,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        *,
        check_hash: bool = True,
    ) -> AsyncGenerator[StepHash, None]:
        """Let the worker know what step it will be working on.

        Parameters
        ----------
        step
            The step to validate, skip or run.
        inp_hashes
            List of tuples of input paths and their file hashes.
        env_vars
            List of environment variable names used by the step.
        check_hash
            If `True`, unexpected changes in input files will cause an error.
        """
        error_pages = []
        try:
            new_step_hash = await self.client.call.new_step(
                step.i, step.label, inp_hashes, env_vars, check_hash
            )
        except RPCError as exc:
            error_pages.append(("RPC Error", str(exc)))
            new_step_hash = None

        if new_step_hash is None and check_hash:
            # The hashes of the input files on disk differ from those in the database,
            # or some inputs were deleted.
            # As this must be due to an external cause and it breaks the workflow,
            # the step will be flagged as failed.
            async with self.dblock:
                step.completed(None)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.client.call.report()
            # Changes to inputs are suspect and can break everything.
            # Therefore, the run phase is ended gracefully by draining the scheduler.
            self.scheduler.drain()
            await self.reporter(
                "ERROR", "The scheduler has been drained due to unexpected input changes."
            )
            yield None
        else:
            try:
                yield new_step_hash
            except RPCError as exc:
                error_pages.append(("RPC Error", str(exc)))

        if not await self.client.call.is_worker_done():
            error_pages.append(
                ("Step not finalized by worker client", "".join(traceback.format_stack()))
            )
        if len(error_pages) > 0:
            async with self.dblock:
                step.completed(None)
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
            await self.reporter("ERROR", step.label, error_pages)
            await self.client.call.unset_step()

    async def compute_out_step_hash(
        self, step: Step, step_hash: StepHash
    ) -> tuple[StepHash, list[tuple[str, FileHash]]]:
        """Compute the output part of a step hash.

        Parameters
        ----------
        step
            The step to compute the hash for.
        step_hash
            An initial step hash with input digest only.

        Returns
        -------
        new_step_hash
            The newly computed hash of the step.
        new_out_hashes
            List of tuples of *changed* output paths and their file hashes.
        """
        async with self.dblock:
            out_hashes = list(step.out_paths(yield_hash=True))
        return await self.client.call.compute_out_step_hash(step_hash, out_hashes)

    async def compute_full_step_hash(
        self, step: Step
    ) -> tuple[StepHash, list[tuple[str, FileHash]], list[tuple[str, FileHash]]]:
        """Compute new step hash, with (updated) out file hashes and env vars.

        Parameters
        ----------
        step
            The step to compute the hash for.
        step_hash
            An initial step hash with input digest only.
            If given, the intention is to skip the step and only the output digest
            is computed. If None, the step has been executed and the full hash is computed.

        Returns
        -------
        new_step_hash
            The newly computed hash of the step.
        new_inp_hashes
            List of tuples of *changed* input paths and their file hashes.
        new_out_hashes
            List of tuples of *changed* output paths and their file hashes.
        """
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
        return await self.client.call.compute_full_step_hash(inp_hashes, env_vars, out_hashes)

    async def shutdown(self):
        """Shutdown the worker process."""
        await self.client.call.shutdown()

    async def interrupt(self, sig: int):
        """Interrupt the step currently running, if any."""
        await self.client.call.interrupt(sig)


#
# In the worker process
#


@attrs.define
class WorkerStep:
    """Information on the current step that a worker is working on."""

    i: int = attrs.field()
    """The index from the node table for the step, used to set the STEPUP_STEP_I."""

    action: str = attrs.field()
    """The action to be executed for the step."""

    workdir: Path = attrs.field()
    """The working directory where the action will be executed."""

    stdout: str = attrs.field(init=False, default="")
    """The standard output captured from the action execution."""

    stderr: str = attrs.field(init=False, default="")
    """The standard error captured from the action execution."""

    perf_info: str = attrs.field(init=False, default="")
    """Performance information collected during the action execution."""

    inp_messages: list = attrs.field(init=False, factory=list)
    """Messages related to input validation issues: unexpected changes and deleted inputs."""

    inp_digest: bytes = attrs.field(init=False, default=b"")
    """The input digest, which can be useful for some actions.

    They may use this to decide if cached results from a previously interrupted run
    of the same stepo are still valid.
    This can also be useful when action submit jobs to a scheduler,
    to decide if a running job is still valid.
    """

    out_missing: list = attrs.field(init=False, factory=list)
    """List of expected output files that were not created."""

    rescheduled_info: str = attrs.field(init=False, default="")
    """Information about why the step was rescheduled."""

    thread: threading.Thread | None = attrs.field(init=False, default=None)
    """Thread that is running the action."""

    returncode: int | None = attrs.field(init=False, default=None)
    """The return code from the action."""

    success: bool = attrs.field(init=False, default=True)
    """Flag indicating whether the step was handled successfully.

    This is relevant for both skipping and executing steps.
    """

    @property
    def description(self):
        """A shorter form of the full action, without module name."""
        return self.action if self.workdir == "./" else f"{self.action}  # wd={self.workdir}"


PYCODE_WRAPPER = """\
import sys
import runpy
from stepup.core.api import amend
from stepup.core.utils import get_local_import_paths
sys.argv = {argv}
try:
    runpy.run_path({script}, run_name="__main__")
finally:
    amend(inp=get_local_import_paths())
"""


class WorkThread(threading.Thread):
    """Thread to run actions in the worker process."""

    def __init__(self, action: str):
        super().__init__()
        self.action = action
        self.returncode = 1
        self.done = asyncio.Event()
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.pid_queue = queue.Queue(1)

    def run(self):
        try:
            action_parts = self.action.split(" ", 1)
            if len(action_parts) == 1:
                action_name = action_parts[0]
                argstr = ""
            else:
                action_name, argstr = self.action.split(" ", 1)
            # Load the action entry point.
            action_funcs = entry_points(group="stepup.actions", name=action_name)
            if len(action_funcs) == 0:
                raise ValueError(f"Action '{action_name}' not found")
            if len(action_funcs) > 1:
                raise AssertionError(f"Multiple actions found for '{action_name}'")
            action_func = next(iter(action_funcs)).load()
            # Run some sanity checks
            if not callable(action_func):
                raise TypeError(f"Action {action_name} is not callable.")
            # Check if the function exists and has the right signature
            sgn = inspect.signature(action_func)
            if sgn.return_annotation is not int:
                raise TypeError(f"Action {action_name} does not return an int.")
            kwargs = {"argstr": argstr}
            if "work_thread" in sgn.parameters:
                kwargs["work_thread"] = self
            # Finally run the action
            returncode = action_func(**kwargs)
            if not isinstance(returncode, int):
                raise TypeError(f"Action {action_name} does not return an int.")
            self.returncode = returncode
        except BaseException as exc:  # noqa: BLE001
            # Catch all exceptions and print them to stderr.
            traceback.print_exc(file=sys.stderr)
            self.returncode = exc.code if isinstance(exc, SystemExit) else 1
        finally:
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.done.set)

    def runsh(self, argstr: str, stdin: str | None = None) -> tuple[int, str | None, str | None]:
        """Run a shell command in a subprocess of the worker process.

        Parameters
        ----------
        argstr
            The command to execute in the shell.
        stdin
            Standard input to the command. If `None`, stdin is closed.

        Returns
        -------
        returncode
            The return code of the command.
        stdout
            The standard output of the command.
        stderr
            The standard error of the command.
        """
        # Sanity check of the executable (if it can be found)
        executable = Path(shlex.split(argstr)[0])
        if not has_shebang(executable):
            print(
                f"Script does not start with a shebang: {executable} (wd={Path.cwd()})",
                file=sys.stderr,
            )
            return 1, None, None
        p = subprocess.Popen(
            argstr,
            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            encoding="utf-8",
            errors="ignore",
            env=os.environ,
        )
        self.pid_queue.put_nowait(p.pid)
        stdout, stderr = p.communicate(stdin)
        with contextlib.suppress(queue.Empty):
            self.pid_queue.get_nowait()
        return p.returncode, stdout, stderr

    def runsh_verbose(self, argstr: str, stdin: str | None = None) -> int:
        """Same as `runsh`, but print stuff and only return the returncode."""
        returncode, stdout, stderr = self.runsh(argstr, stdin)
        if returncode != 0:
            print(f"Command failed with return code {returncode}: {argstr}", file=sys.stderr)
        if stdout is not None and len(stdout) > 0:
            sys.stdout.write(stdout)
        if stderr is not None and len(stderr) > 0:
            sys.stderr.write(stderr)
        return returncode

    def runpy(self, script: str, args: list[str]) -> int:
        """Run a Python script and amend all local imports as inputs."""
        # Sanity check of the executable (if it can be found)
        if not has_shebang(Path(script)):
            print(
                f"Script does not start with a shebang: {script} (wd={Path.cwd()})",
                file=sys.stderr,
            )
            return 1
        # Run the script
        return self.runsh_verbose(
            f"{sys.executable} -",
            PYCODE_WRAPPER.format(argv=repr([script, *args]), script=repr(script)),
        )


def has_shebang(executable: Path) -> bool:
    """Return `True` if a script has a shebang and is executable, or if the file is not a script."""
    # See https://en.wikipedia.org/wiki/Shebang_%28Unix%29
    if not executable.is_file():
        # The executable is probably in the PATH,
        # i.e. not a custom script, so not checking
        return True
    # Check if the file is executable
    if not executable.access(os.X_OK):
        # This is not a script, so not checking the shebang.
        return False
    # Check if the file is binary.
    # https://stackoverflow.com/a/7392391
    with open(executable, "rb") as fh:
        head = fh.read(1024)
    printable_text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    # Check if the file is binary by translating non-text characters
    if bool(head.translate(None, printable_text_chars)):
        # This is unlikely to be a script, so not checking the shebang.
        return True
    return head[:3] == b"#!/"


@attrs.define
class WorkerHandler:
    """RPC Handler in the worker process to respond to requests from the WorkerClient."""

    reporter: ReporterClient = attrs.field()
    show_perf: bool = attrs.field()
    explain_rerun: bool = attrs.field()
    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    step: WorkerStep | None = attrs.field(init=False, default=None)

    @allow_rpc
    def shutdown(self):
        self.stop_event.set()

    @allow_rpc
    def interrupt(self, sig: int):
        if self.step is not None and self.step.thread is not None:
            # Get process IDs launched in the thread.
            pids = []
            while True:
                try:
                    pids.append(self.step.thread.pid_queue.get(block=False))
                except queue.Empty:
                    break
            if len(pids) > 0:
                # Kill the process IDs first
                for pid in pids:
                    with contextlib.suppress(ProcessLookupError):
                        print(f"Interrupting process {pid} with singal {sig}", file=sys.stderr)
                        os.kill(pid, sig)
            else:
                # Thread running, raise KeyboardInterrupt in the thread.
                # See https://stackoverflow.com/a/325528
                print("No worker subprocesses, interrupting thread", file=sys.stderr)
                res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self.step.thread.ident), ctypes.py_object(KeyboardInterrupt)
                )
                if res == 0:
                    raise RPCError("Failed to interrupt the worker thread.")

    @allow_rpc
    async def new_step(
        self,
        i: int,
        label: str,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        check_hash: bool = True,
    ) -> StepHash | None:
        """Prepare the worker for a new step.

        Parameters
        ----------
        i
            The index from the node table for the step.
        label
            The label of the step to prepare for (contains command and workdir).
        inp_hashes
            List of tuples of input paths and their file hashes.
        env_vars
            List of environment variable names used by the step.
        check_hash
            If `True`, unexpected changes in input files will cause an error.

        Returns
        -------
        step_hash
            The hash of the step, with the input part already computed, if available.
            `None` is returned if some inputs are missing or have changed unexpectedly.
        """
        if self.step is not None:
            raise RPCError(
                "Worker cannot initiate two steps at the same time. "
                f"Still working on {self.step.action}"
            )

        # Create the step
        action, workdir = split_step_label(label)
        self.step = WorkerStep(i, action, workdir)

        # Create initial StepHash
        return self.compute_inp_step_hash(inp_hashes, env_vars, check_hash)[0]

    def compute_inp_step_hash(
        self,
        old_inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        check_hash: bool = True,
    ) -> tuple[StepHash | None, list[tuple[str, FileHash]]]:
        """Compute the input part of a step hash.

        Parameters
        ----------
        old_inp_hashes
            List of tuples of input paths and their file hashes.
            These should be up to date. If any changes are observed, something went wrong.
        env_vars
            List of environment variable names to include in the hash.
        check_hash
            If `True`, unexpected changes in input files will cause an error.

        Returns
        -------
        step_hash
            The hash of the step, with the input part already computed, if available.
            `None` is returned if some inputs are missing or have changed unexpectedly.
        """
        # Check the input hashes
        messages = []
        new_inp_hashes = []
        all_inp_hashes = []
        for path, old_file_hash in old_inp_hashes:
            new_file_hash = old_file_hash.regen(path)
            all_inp_hashes.append((path, new_file_hash))
            if check_hash and new_file_hash != old_file_hash:
                if new_file_hash.is_unknown:
                    messages.append(f"Input vanished unexpectedly: {path} ")
                else:
                    messages.append(
                        f"Input changed unexpectedly: {path} "
                        + fmt_file_hash_diff(old_file_hash, new_file_hash)
                    )
                new_inp_hashes.append((path, new_file_hash))

        # If there are unexpected issues with inputs, bail out.
        if len(messages) > 0:
            self.step.inp_messages = messages
            self.step.success = False
            return None, new_inp_hashes

        # Add the environment variables to the hash
        env_var_values = [(env_var, os.environ.get(env_var)) for env_var in env_vars]

        # Create the StepHash
        label = self.step.action
        if self.step.workdir != "./":
            label += f"  # wd={self.step.workdir}"
        result = StepHash.from_inp(f"{label}", self.explain_rerun, all_inp_hashes, env_var_values)

        # Copy the inp_digest, because it can be useful for some actions.
        self.step.inp_digest = result.inp_digest
        return result, []

    @allow_rpc
    def compute_out_step_hash(
        self,
        step_hash: StepHash | None,
        old_out_hashes: list[tuple[str, FileHash]],
    ) -> tuple[StepHash | None, list[tuple[str, FileHash]]]:
        """Recompute output file hashes and update the step hash.

        Parameters
        ----------
        step_hash
            The hash of the step, with the input part already computed, if available.
        out_hashes
            List of tuples of output paths and their file hashes.
            These hashes may change because the outputs may have been updated or removed.

        Returns
        -------
        new_step_hash
            The updated hash of the step, or `None` if some outputs are missing.
        new_out_hashes
            List of tuples of output paths and their file hashes.
            These are the updated hashes of the output files.
            Unchanged ones are not included.
        """
        # Check the output hashes
        new_out_hashes = []
        all_out_hashes = []
        for path, old_file_hash in sorted(old_out_hashes):
            new_file_hash = old_file_hash.regen(path)
            all_out_hashes.append((path, new_file_hash))
            if new_file_hash != old_file_hash:
                new_out_hashes.append((path, new_file_hash))
            if new_file_hash.is_unknown:
                self.step.out_missing.append(path)
                self.step.success = False

        # Update the step hash
        if step_hash is not None:
            step_hash = step_hash.evolve_out(all_out_hashes)
        return step_hash, new_out_hashes

    @allow_rpc
    def compute_full_step_hash(
        self,
        inp_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        out_hashes: list[tuple[str, FileHash]],
    ) -> tuple[StepHash, list[tuple[str, FileHash]], list[tuple[str, FileHash]]]:
        """Recompute file and step hashes.

        Parameters
        ----------
        old_inp_hashes
            List of tuples of input paths and their file hashes.
            These should be up to date. If any changes are observed, something went wrong.
        old_out_hashes
            List of tuples of output paths and their file hashes.
            These hashes may change because the outputs may have been updated or removed.
        env_vars
            List of environment variable names to include in the hash.

        Returns
        -------
        step_hash
            The newly computed hash of the step,
            or None of there are unexpected changes in the input files.
        new_inp_hashes
            List of tuples of input paths and their file hashes.
            These are the updated hashes of the input files.
            Unchanged ones are not included.
        new_out_hashes
            List of tuples of output paths and their file hashes.
            These are the updated hashes of the output files.
            Unchanged ones are not included.
        """
        step_hash, new_inp_hashes = self.compute_inp_step_hash(inp_hashes, env_vars)
        step_hash, new_out_hashes = self.compute_out_step_hash(step_hash, out_hashes)
        return step_hash, new_inp_hashes, new_out_hashes

    @allow_rpc
    async def outdated_amended(self, old_hash: StepHash, new_hash: StepHash):
        """Report that the amended info of the step is outdated."""
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            pages = [("Outdated amended step information", page_change)]
            if len(page_same) > 0:
                pages.append(("Remained the same (or missing)", page_same))
            await self.reporter("DROPAMEND", self.step.description, pages)
        self.step = None

    @allow_rpc
    async def unset_step(self):
        """If the amended info is valid, there is nothing to report."""
        self.step = None

    @allow_rpc
    async def run(self):
        await self.reporter("START", self.step.description)
        await self.reporter.start_step(self.step.description, self.step.i)

        # For internal use in actions:
        os.environ["STEPUP_STEP_I"] = str(self.step.i)
        # Client code may use the following:
        os.environ["STEPUP_STEP_INP_DIGEST"] = self.step.inp_digest.hex()
        os.environ["ROOT"] = str(Path.cwd().relpath(self.step.workdir))
        os.environ["HERE"] = str(self.step.workdir.relpath())
        # Note: the variables defined here should be listed in stepup.core.api.getenv

        # Create IO redirection for stdout and stderr
        step_err = io.StringIO()
        step_out = io.StringIO()
        with (
            contextlib.chdir(self.step.workdir),
            contextlib.redirect_stderr(step_err),
            contextlib.redirect_stdout(step_out),
        ):
            if self.show_perf:
                ru_initial = resource.getrusage(resource.RUSAGE_CHILDREN)
                pt_initial = perf_counter()
            self.step.thread = WorkThread(self.step.action)
            self.step.thread.start()
            await self.step.thread.done.wait()
            self.step.thread.join()
            self.step.returncode = self.step.thread.returncode
            self.step.thread = None
        self.step.stdout = step_out.getvalue()
        self.step.stderr = step_err.getvalue()

        # Clean up environment variables (to avoid potential confusion)
        del os.environ["STEPUP_STEP_I"]
        del os.environ["STEPUP_STEP_INP_DIGEST"]
        del os.environ["ROOT"]
        del os.environ["HERE"]

        # Process results of the step.
        if self.show_perf:
            ru_final = resource.getrusage(resource.RUSAGE_CHILDREN)
            utime = ru_final.ru_utime - ru_initial.ru_utime
            stime = ru_final.ru_stime - ru_initial.ru_stime
            wtime = perf_counter() - pt_initial
            ru_lines = [
                f"User CPU time [s]:   {utime:9.4f}",
                f"System CPU time [s]: {stime:9.4f}",
                f"Total CPU time [s]:  {utime + stime:9.4f}",
                f"Wall time [s]:       {wtime:9.4f}",
            ]
            self.step.perf_info = "\n".join(ru_lines)
        self.step.success = self.step.returncode == 0

    @allow_rpc
    def get_success(self) -> bool:
        return self.step.success

    @allow_rpc
    async def rescheduled(self, rescheduled_info: str):
        self.step.rescheduled_info = rescheduled_info
        # Erase other error info to keep the screen output concise.
        self.step.success = True
        self.step.stderr = b""

    @allow_rpc
    async def report(self):
        pages = []
        if not self.step.success:
            if self.step.workdir == "./":
                command = f"stepup act {self.step.action}"
            else:
                command = f"(cd {self.step.workdir} && stepup act {self.step.action})"
            lines = [f"Command               {command}"]
            if self.step.returncode is not None:
                lines.append(f"Return code           {self.step.returncode}")
            pages.append(("Step info", "\n".join(lines)))
        if len(self.step.perf_info) > 0:
            pages.append(("Performance details", self.step.perf_info))
        if self.step.rescheduled_info != "":
            pages.append(
                (
                    "Rescheduling due to unavailable amended inputs",
                    self.step.rescheduled_info,
                )
            )
        else:
            if len(self.step.inp_messages) > 0:
                self.step.inp_messages.sort()
                pages.append(("Invalid inputs", "\n".join(self.step.inp_messages)))
            if len(self.step.out_missing) > 0:
                self.step.out_missing.sort()
                pages.append(("Expected outputs not created", "\n".join(self.step.out_missing)))
        stdout = self.step.stdout.rstrip()
        if len(stdout) > 0:
            pages.append(("Standard output", stdout))
        stderr = self.step.stderr.rstrip()
        if len(stderr) > 0:
            pages.append(("Standard error", stderr))
        if self.step.rescheduled_info != "":
            action = "RESCHEDULE"
        elif self.step.success:
            action = "SUCCESS"
        else:
            action = "FAIL"
        await self.reporter.stop_step(self.step.i)
        await self.reporter(action, self.step.description, pages)
        self.step = None

    @allow_rpc
    async def skip(self, step_hash: StepHash):
        pages = []
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(step_hash, step_hash)
            if len(page_change) > 0:
                raise AssertionError(
                    "A skipped step cannot have changes in inputs, env vars or outputs."
                )
            if len(page_same) > 0:
                pages.append(("No changes observed", page_same))
        await self.reporter("SKIP", self.step.description, pages)
        self.step = None

    @allow_rpc
    async def noskip(self, old_hash: StepHash, new_hash: StepHash):
        if self.explain_rerun:
            pages = []
            if len(self.step.out_missing) > 0:
                pages.append(("Missing output files", "\n".join(self.step.out_missing)))
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            if len(page_change) > 0:
                pages.append(("Changes causing rerun", page_change))
            if len(page_same) > 0:
                pages.append(("Remained the same", page_same))
            await self.reporter("NOSKIP", self.step.description, pages)
        self.step = None

    @allow_rpc
    def is_worker_done(self) -> bool:
        return self.step is None


def parse_args():
    parser = argparse.ArgumentParser(
        prog="stepup-worker", description="Launch and monitor running steps."
    )
    parser.add_argument("director_socket", type=Path, help="Socket of the director")
    parser.add_argument("worker_socket", type=Path, help="Socket of the worker (to be created)")
    parser.add_argument("worker_idx", type=int, help="Worker index")
    parser.add_argument(
        "--reporter",
        "-r",
        type=Path,
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=False,
        action="store_true",
        help="Add performance details after every completed step.",
    )
    parser.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action="store_true",
        help="Explain for every step why it cannot be skipped.",
    )
    return parser.parse_args()


async def async_main():
    args = parse_args()
    os.environ["STEPUP_DIRECTOR_SOCKET"] = args.director_socket
    os.environ["STEPUP_ROOT"] = str(Path.cwd())
    print(f"PID {os.getpid()}", file=sys.stderr)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        # Create the worker handler for the RPC server.
        handler = WorkerHandler(reporter, args.show_perf, args.explain_rerun)
        await serve_socket_rpc(handler, args.worker_socket, handler.stop_event)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
