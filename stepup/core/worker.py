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
import os
import resource
import shlex
import signal
import sys
from copy import copy
from functools import partial
from time import perf_counter

import attrs
from path import Path

from stepup.core.utils import check_inp_path

from .enums import StepState
from .exceptions import RPCError
from .file import FileState
from .hash import FileHash, StepHash, compare_step_hashes, report_hash_diff
from .reporter import ReporterClient
from .rpc import AsyncRPCClient, allow_rpc, serve_stdio_rpc
from .step import Step
from .utils import DBLock
from .workflow import Workflow

__all__ = ("WorkerClient", "WorkerHandler", "WorkerStep")


#
# In the main director process
#


@attrs.define
class WorkerClient:
    """Client interface to a worker, used by the director process."""

    # The workflow that the client is interacting with.
    workflow: Workflow = attrs.field()

    # Lock for workflow database access.
    dblock: DBLock = attrs.field()

    # A reporter to send progress and terminal output to.
    reporter: ReporterClient = attrs.field()

    # The path of the directory socket.
    # A step being executed by a worker needs this socket extend the workflow.
    director_socket_path: str = attrs.field()

    # Flag to enable detailed CPU usage of each step.
    show_perf: bool = attrs.field()

    # Flat to explain why a step could not be skipped.
    explain_rerun: bool = attrs.field()

    # The integer index of the worker (in the list of workers kept by the Runner instance).
    idx: int = attrs.field(converter=int)

    # The RPC client to communicate with the worker process.
    client: AsyncRPCClient | None = attrs.field(init=False, default=None)

    #
    # Setup and teardown
    #

    async def boot(self):
        args = [
            "-m",
            "stepup.core.worker",
            self.director_socket_path,
            str(self.idx),
        ]
        if self.show_perf:
            args.append("--show-perf")
        if self.explain_rerun:
            args.append("--explain-rerun")
        if self.reporter.socket_path is not None:
            args.append(f"--reporter={self.reporter.socket_path}")
        self.client = await AsyncRPCClient.subprocess(
            sys.executable,
            *args,
        )

    async def close(self):
        await self.client.close()

    #
    # Functions called by jobs
    #

    async def validate_amended_job(self, step: Step) -> bool:
        """Test if the inputs (hashes) have changed, which would invalidate the amended step info.

        Parameters
        ----------
        step
            The step whose amended inputs need validation.

        Returns
        -------
        must_run
            True if the amended inputs are invalid.
            This can be fixed by running the step again.
        """
        async with self.dblock:
            step_hash = step.get_hash()
            if step_hash is None:
                # When there is no hash, we cannot check if the inputs have changed.
                # It needs to run anyway.
                step.clean_before_run()
                return True

        # Compute the hash and check for changes in inputs
        await self.new_step(step)
        new_step_hash = await self.compute_step_hash(
            step, log_missing_out=False, update_out_hashes=False
        )
        if new_step_hash is None:
            raise AssertionError("compute_step_hash returned None")
        if step_hash.inp_digest != new_step_hash.inp_digest:
            # Inputs have changed, so must run.
            await self.client.call.outdated_amended(step_hash, new_step_hash)
            async with self.dblock:
                step.clean_before_run()
                step.delete_hash()
            return True

        # No inputs have changed, send back to scheduler without making a fuzz.
        async with self.dblock:
            step.set_state(StepState.PENDING)
            step.queue_if_appropriate()
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        await self.client.call.valid_amended()
        return False

    async def try_skip_job(self, step: Step) -> bool:
        """Try skipping a step.

        Parameters
        ----------
        step
            The step to skip (if outputs are up to date).

        Returns
        -------
        must_run
            True if skipping failed and the steps needs to be rescheduled for running immediately.
        """
        async with self.dblock:
            step_hash = step.get_hash()
        if step_hash is None:
            raise AssertionError("Step without hash cannot be skipped")
        await self.new_step(step)
        if not await self.inputs_valid(step):
            return True
        new_step_hash = await self.compute_step_hash(
            step, log_missing_out=True, update_out_hashes=False
        )
        if new_step_hash is None:
            raise AssertionError("compute_step_hash returned None")
        # If files changes or outputs are missing, skipping is not possible
        success = await self.client.call.get_success()
        if step_hash.digest != new_step_hash.digest or not success:
            await self.client.call.noskip(step_hash, new_step_hash)
            return True
        # All checks passed. No need to run the step, just simulate the creation of the products.
        await self.client.call.skip(step_hash)
        step.completed(True, new_step_hash)
        async with self.dblock:
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        return False

    async def run_job(self, step: Step):
        """Run a step.

        Parameters
        ----------
        step
            The step to run.
        """
        # Create the step
        await self.new_step(step)
        if not await self.inputs_valid(step):
            return

        # Run the step
        async with self.dblock:
            step.set_state(StepState.RUNNING)
            step.clean_before_run()
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        await self.client.call.run()

        # Check the result.
        # Always updating hashes, even for failed commands:
        # This way, outputs can be removed safely if they are not needed anymore.
        new_step_hash = await self.compute_step_hash(
            step, log_missing_out=True, update_out_hashes=True
        )
        success = await self.client.call.get_success()
        async with self.dblock:
            rescheduled_info = step.completed(success, new_step_hash)
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)

        # Report reason for rescheduling if relevant
        if rescheduled_info != "":
            await self.client.call.rescheduled(rescheduled_info)
        await self.client.call.report()

    #
    # Wrappers around RPCs
    #

    async def new_step(self, step: Step):
        """Let the worker know what step it will be working on."""
        async with self.dblock:
            command, workdir = step.get_command_workdir()
        await self.client.call.new_step(step.key(), command, workdir)

    async def inputs_valid(self, step: Step):
        """Return True when all inputs are present and valid.

        Files must be existing files and directories must be existing directories."""
        async with self.dblock:
            inp_paths = list(step.inp_paths(yield_state=True))
        if not await self.client.call.inputs_valid(inp_paths):
            async with self.dblock:
                rescheduled_info = step.completed(False, None)
                step_counts = self.workflow.get_step_counts()
            if rescheduled_info != "":
                raise AssertionError("Step rescheduled while inputs are invalid.")
            await self.reporter.update_step_counts(step_counts)
            await self.client.call.report()
            return False
        return True

    async def compute_step_hash(
        self, step: Step, *, log_missing_out: bool, update_out_hashes: bool
    ) -> StepHash:
        """Compute new step hash, with updated file hashes and env vars.

        Parameters
        ----------
        step
            The step to compute the hash for.
        log_missing_out
            Log missing output files, which should have been created by the step.
        update_out_hashes
            Update the hashes of the output files, only relevant after a successful or failed run.

        Returns
        -------
        new_step_hash
            The newly computed hash of the step.
        """
        async with self.dblock:
            inp_paths = list(step.inp_paths(yield_hash=True))
            out_paths = list(step.out_paths(yield_hash=True))
            env_vars = list(step.env_vars())
        changed_out_hashes, new_step_hash = await self.client.call.compute_step_hash(
            inp_paths,
            out_paths,
            env_vars,
            log_missing_out,
        )
        if update_out_hashes:
            async with self.dblock:
                self.workflow.update_file_hashes(changed_out_hashes)
        return new_step_hash

    async def shutdown(self):
        await self.client.call.shutdown()

    async def kill(self, sig: int):
        await self.client.call.kill(sig)


#
# In the worker process
#


@attrs.define
class WorkerStep:
    """Information on the current step that a worker is working on."""

    key: str = attrs.field()
    command: str = attrs.field()
    workdir: Path = attrs.field()
    proc: asyncio.subprocess.Process | None = attrs.field(init=False, default=None)
    stdout: bytes = attrs.field(init=False, default=b"")
    stderr: bytes = attrs.field(init=False, default=b"")
    perf_info: str = attrs.field(init=False, default="")
    inp_missing: list = attrs.field(init=False, factory=list)
    out_missing: list = attrs.field(init=False, factory=list)
    rescheduled_info: str = attrs.field(init=False, default="")
    returncode: int | None = attrs.field(init=False, default=None)
    success: bool = attrs.field(init=False, default=True)

    @property
    def description(self):
        return self.command if self.workdir == "./" else f"{self.command}  # wd={self.workdir}"


@attrs.define
class WorkerHandler:
    """RPC Handler in the worker process to respond to requests from the WorkerClient."""

    director_socket_path: str
    reporter: ReporterClient = attrs.field()
    show_perf: bool = attrs.field()
    explain_rerun: bool = attrs.field()
    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    step: WorkerStep | None = attrs.field(init=False, default=None)

    @allow_rpc
    def shutdown(self):
        self.stop_event.set()

    @allow_rpc
    def kill(self, sig: int):
        if self.step is not None and self.step.proc is not None:
            self.step.proc.send_signal(sig)

    @allow_rpc
    async def new_step(self, step_key: str, command: str, workdir: Path):
        if self.step is not None:
            raise RPCError(
                "Worker cannot initiate two steps at the same time. "
                f"Still working on {self.step.command}"
            )
        self.step = WorkerStep(step_key, command, workdir)

    @allow_rpc
    def inputs_valid(self, inp_paths: list[tuple[Path, FileState]]) -> bool:
        """Check the presence of all paths and whether they are files or directories.

        This will also record a list of problems in inp_missing for on-screen reporting.

        Parameters
        ----------
        inp_paths
            Tuples of path and FileState. Paths ending with a trailing separator must be directories
            and paths without must be files.

        Returns
        -------
        valid
            True when inputs are present and valid.
        """
        inp_missing = self.step.inp_missing
        for path, state in inp_paths:
            path = Path(path)
            message = check_inp_path(path)
            if message is not None:
                inp_missing.append(f"{state.name} {message}: {path}")
                self.step.success = False
        return len(inp_missing) == 0

    @allow_rpc
    def compute_step_hash(
        self,
        old_inp_hashes: list[tuple[str, FileHash]],
        old_out_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        log_missing_out: bool,
    ) -> tuple[list[tuple[str, FileHash]], list[tuple[str, FileHash]], StepHash]:
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
        log_missing_out
            Log missing output files, which should have been created by the step.

        Returns
        -------
        new_out_hashes
            List of tuples of output paths and their file hashes.
            These are the updated hashes of the output files.
            Unchanged ones are not included.
        step_hash
            The newly computed hash of the step.
        """
        # Have inputs changed?
        inp_hashes = []
        for path, file_hash in old_inp_hashes:
            old_file_hash = copy(file_hash)
            changed = file_hash.update(path)
            # If the step succeeded, the input file hash should be up-to-date.
            # A step may fail due to missing amended inputs, in which case
            # these new inputs have no up-to-date hashes yet.
            if changed is True and self.step.success:
                raise AssertionError(
                    "Input file hash changed unexpectedly: "
                    + report_hash_diff("", path, old_file_hash, file_hash)[1][1]
                )
            inp_hashes.append((path, file_hash))
        # Have outputs changed?
        out_hashes = []
        new_out_hashes = []
        self.step.out_missing = []
        for path, file_hash in sorted(old_out_hashes):
            change = file_hash.update(path)
            if change is True:
                new_out_hashes.append((path, file_hash))
            elif change is None and log_missing_out:
                self.step.out_missing.append(path)
                self.step.success = False
            out_hashes.append((path, file_hash))
        # Update step hash
        env_var_values = [(env_var, os.environ.get(env_var)) for env_var in env_vars]
        step_hash = StepHash.from_step(
            self.step.key, self.explain_rerun, inp_hashes, env_var_values, out_hashes
        )
        return new_out_hashes, step_hash

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
    async def valid_amended(self):
        """If the amended info is valid, there is nothing to report."""
        self.step = None

    @allow_rpc
    async def run(self):
        await self.reporter("START", self.step.description)

        # Sanity check of the executable (if it can be found)
        if not has_shebang(self.step.workdir / shlex.split(self.step.command)[0]):
            self.step.stdout = b""
            self.step.stderr = b"Script does not start with a shebang."
            self.step.returncode = 1
            self.step.success = False
            return

        # Actual execution
        stepup_root = Path.cwd()
        env = os.environ | {
            "STEPUP_DIRECTOR_SOCKET": self.director_socket_path,
            "STEPUP_STEP_KEY": self.step.key,
            "STEPUP_ROOT": stepup_root,
            "ROOT": Path.cwd().relpath(self.step.workdir),
            "HERE": self.step.workdir.relpath(),
        }
        if self.show_perf:
            ru_initial = resource.getrusage(resource.RUSAGE_CHILDREN)
            pt_initial = perf_counter()
        proc = await asyncio.create_subprocess_shell(
            self.step.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=self.step.workdir,
        )
        self.step.proc = proc

        # Process results of the step.
        self.step.stdout, self.step.stderr = await proc.communicate()
        self.step.proc = None
        self.step.returncode = proc.returncode
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
            lines = [f"Command               {self.step.command}"]
            if self.step.workdir != "./":
                lines.append(f"Working directory     {self.step.workdir}")
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
            if len(self.step.inp_missing) > 0:
                self.step.inp_missing.sort()
                pages.append(("Invalid inputs", "\n".join(self.step.inp_missing)))
            if len(self.step.out_missing) > 0:
                self.step.out_missing.sort()
                pages.append(("Expected outputs not created", "\n".join(self.step.out_missing)))
        stdout = self.step.stdout.decode("utf-8", errors="ignore").rstrip()
        if len(stdout) > 0:
            pages.append(("Standard output", stdout))
        stderr = self.step.stderr.decode("utf-8", errors="ignore").rstrip()
        if len(stderr) > 0:
            pages.append(("Standard error", stderr))
        if self.step.rescheduled_info != "":
            action = "RESCHEDULE"
        elif self.step.success:
            action = "SUCCESS"
        else:
            action = "FAIL"
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


def has_shebang(executable: Path) -> bool:
    """Return `True` if a script has a shebang or if the file is not a script."""
    # See https://en.wikipedia.org/wiki/Shebang_%28Unix%29
    if not executable.is_file():
        # The executable is probably in the PATH,
        # i.e. not a custom script, so not checking
        return True
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


def parse_args():
    parser = argparse.ArgumentParser(
        prog="stepup-worker", description="Launch and monitor running steps."
    )
    parser.add_argument("director_socket", help="Socket of the director")
    parser.add_argument("worker_idx", type=int, help="Worker index")
    parser.add_argument(
        "--reporter",
        "-r",
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=False,
        action="store_true",
        help="Add performance details after completed step.",
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
    with contextlib.ExitStack() as stack:
        ferr = stack.enter_context(open(f".stepup/worker{args.worker_idx}.log", "w"))
        stack.enter_context(contextlib.redirect_stderr(ferr))
        print(f"PID {os.getpid()}", file=sys.stderr)
        async with ReporterClient.socket(args.reporter_socket) as reporter:
            # Create the worker handler for the RPC server (and to handle signals).
            handler = WorkerHandler(
                args.director_socket, reporter, args.show_perf, args.explain_rerun
            )
            # Install signal handlers
            loop = asyncio.get_running_loop()
            for sig in signal.SIGINT, signal.SIGTERM:
                loop.add_signal_handler(sig, partial(handler.kill, sig))
            # Serve RPC
            await serve_stdio_rpc(handler)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
