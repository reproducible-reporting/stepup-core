# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
import os
import resource
import sys
from time import perf_counter

import attrs
from path import Path

from stepup.core.utils import check_inp_path

from .exceptions import RPCError
from .file import FileState
from .hash import FileHash, StepHash, compare_step_hashes, create_step_hash
from .reporter import ReporterClient
from .rpc import AsyncRPCClient, allow_rpc, serve_stdio_rpc
from .step import Step, StepState
from .workflow import Workflow

__all__ = ("WorkerClient", "WorkerStep", "WorkerHandler")


#
# In the main director process
#


@attrs.define
class WorkerClient:
    """Client interface to a worker, used by the director process."""

    # The workflow that the client is interacting with.
    workflow: Workflow = attrs.field()

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
        log_file = open(f".stepup/logs/worker{self.idx}", "w")  # noqa: SIM115
        args = [
            "-m",
            "stepup.core.worker",
            self.director_socket_path,
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
            stderr=log_file,
        )

    async def close(self):
        await self.client.close()

    #
    # Functions called by jobs
    #

    async def validate_amended_job(self, step_key: str) -> bool:
        """Test if the inputs have changed, which would invalidate the amended step info.

        Parameters
        ----------
        step_key
            The step whose amended inputs need validation.

        Returns
        -------
        must_run
            True if the amended inputs are invalid.
            This can be fixed by running the step again.
        """
        # Create the step.
        step = self.workflow.get_step(step_key)
        if step.hash is None:
            # When there is no hash, we cannot check if the inputs have changed.
            # It needs to run anyway
            step.clean_before_run(self.workflow)
            step.discard_recording()
            return True

        # Compute the hash and check for changes in inputs
        await self.new_step(step)
        new_step_hash = await self.update_hashes(step, log_missing_out=False)
        assert new_step_hash is not None
        if step.hash.inp_digest != new_step_hash.inp_digest:
            # Inputs have changed, so must run.
            await self.client.call.outdated_amended(step.hash, new_step_hash)
            step.clean_before_run(self.workflow)
            step.discard_recording()
            return True

        # No inputs have changed, send back to scheduler without making a fuzz.
        step.set_state(self.workflow, StepState.PENDING)
        await self.reporter.update_step_counts(self.workflow.get_step_counters())
        await self.client.call.valid_amended()
        step.queue_if_appropriate(self.workflow)
        return False

    async def try_replay_job(self, step_key: str) -> bool:
        """Try skipping a step and replaying the recorded actions.

        Parameters
        ----------
        step_key
            The step to skip (if outputs are up to date).

        Returns
        -------
        must_run
            True if skipping failed and the steps needs to be rescheduled for running immediately.
        """
        step = self.workflow.get_step(step_key)
        assert step.hash is not None
        assert step.recording is not None
        await self.new_step(step)
        if not await self.inputs_valid(step):
            return False
        new_step_hash = await self.update_hashes(step, log_missing_out=True)
        assert new_step_hash is not None
        # If files changes or outputs are missing, skipping is not possible
        success = await self.client.call.get_success()
        if step.hash.digest != new_step_hash.digest or not success:
            await self.client.call.noskip(step.hash, new_step_hash)
            return True
        # All checks passed. No need to run the step, just simulate the creation of the products.
        step.clean_before_run(self.workflow)
        step.replay_amend(self.workflow)
        step.replay_rest(self.workflow)
        await self.reporter.update_step_counts(self.workflow.get_step_counters())
        await self.client.call.skip(step.hash)
        return False

    async def run_job(self, step_key: str):
        """Run a step.

        Parameters
        ----------
        step_key
            The step to run.
        """
        # Create the step
        step = self.workflow.get_step(step_key)
        await self.new_step(step)
        if not await self.inputs_valid(step):
            return

        # Run the step
        step.set_state(self.workflow, StepState.RUNNING)
        await self.reporter.update_step_counts(self.workflow.get_step_counters())
        step.clean_before_run(self.workflow)
        await self.client.call.run()

        # Check the result.
        # Always updating hashes, even for failed commands:
        # This way, outputs can be removed safely if they are not needed anymore.
        new_step_hash = await self.update_hashes(step, log_missing_out=True)
        success = await self.client.call.get_success()
        missing_amended = step.completed(self.workflow, success, new_step_hash)
        await self.reporter.update_step_counts(self.workflow.get_step_counters())

        # Report reason for rescheduling if relevant
        if len(missing_amended) > 0:
            await self.client.call.list_rescheduled(missing_amended)
        await self.client.call.report()

    #
    # Wrappers around RPCs
    #

    async def new_step(self, step: Step):
        """Let the worker know what step it will be working on."""
        await self.client.call.new_step(step.key, step.command, Path(step.workdir))

    async def inputs_valid(self, step: Step):
        """Return True all inputs are present, files are files and directories are directories."""
        inp_paths = step.get_inp_paths(self.workflow, state=True)
        if not await self.client.call.inputs_valid(inp_paths):
            rescheduled = step.completed(self.workflow, False, None)
            assert not rescheduled
            await self.reporter.update_step_counts(self.workflow.get_step_counters())
            await self.client.call.report()
            return False
        return True

    async def update_hashes(self, step: Step, *, log_missing_out: bool) -> StepHash:
        """Compute new step hash, with updated file hashes and env vars."""
        new_inp_hashes, new_out_hashes, new_step_hash = await self.client.call.update_hashes(
            step.get_inp_paths(self.workflow, file_hash=True),
            step.get_out_paths(self.workflow, file_hash=True),
            list(step.initial_env_vars | step.amended_env_vars),
            log_missing_out,
        )
        for path, file_hash in new_inp_hashes:
            self.workflow.set_file_hash(path, file_hash)
        for path, file_hash in new_out_hashes:
            self.workflow.set_file_hash(path, file_hash)
        return new_step_hash

    async def shutdown(self):
        await self.client.call.shutdown()


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
    inp_amend_missing: list = attrs.field(init=False, factory=list)
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
    async def new_step(self, step_key: str, command: str, workdir: Path):
        if self.step is not None:
            raise RPCError("Worker cannot initiate two steps at the same time.")
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
    def update_hashes(
        self,
        old_inp_hashes: list[tuple[str, FileHash]],
        old_out_hashes: list[tuple[str, FileHash]],
        env_vars: list[str],
        log_missing_out: bool,
    ) -> tuple[list[tuple[str, FileHash]], list[tuple[str, FileHash]], StepHash]:
        """Recompute file and step hashes."""
        # Have inputs changed?
        inp_hashes = []
        new_inp_hashes = []
        for path, file_hash in old_inp_hashes:
            changed = file_hash.update(path)
            if changed is True:
                new_inp_hashes.append((path, file_hash))
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
        step_hash = create_step_hash(
            self.step.key, self.explain_rerun, inp_hashes, env_var_values, out_hashes
        )
        return new_inp_hashes, new_out_hashes, step_hash

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
        self.step.stdout, self.step.stderr = await proc.communicate()
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
    async def list_rescheduled(self, inp_paths: set[str]):
        self.step.inp_amend_missing = sorted(inp_paths)
        # for inp_path, inp_state, orphan in inp_paths:
        #    if inp_state not in (FileState.BUILT, FileState.STATIC):
        #        inp_path_fmt = f"({inp_path})" if orphan else inp_path
        #        self.step.inp_amend_missing.append(f"{inp_state.name} {inp_path_fmt}")

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
        if len(self.step.inp_amend_missing) > 0:
            pages.append(
                (
                    "Rescheduling due to unavailable amended inputs",
                    "\n".join(self.step.inp_amend_missing),
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
        if len(self.step.inp_amend_missing) > 0:
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
            assert len(page_change) == 0
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


def parse_args():
    parser = argparse.ArgumentParser(
        prog="stepup-worker", description="Launch and monitor running steps."
    )
    parser.add_argument("director_socket", help="Socket of the director")
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
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    return parser.parse_args()


async def async_main():
    args = parse_args()
    print(f"PID {os.getpid()}", file=sys.stderr)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        handler = WorkerHandler(args.director_socket, reporter, args.show_perf, args.explain_rerun)
        await serve_stdio_rpc(handler)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
