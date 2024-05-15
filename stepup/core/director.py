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
"""The director process manages the workflow and sends jobs to the worker processes."""

import argparse
import asyncio
import contextlib
import os
import shutil
import sys
import time
import traceback
from decimal import Decimal

import attrs
from path import Path

from stepup.core.file import FileState
from stepup.core.nglob import NGlobMulti

from .exceptions import GraphError
from .reporter import ReporterClient
from .rpc import allow_rpc, serve_socket_rpc
from .runner import Runner
from .scheduler import Scheduler
from .utils import check_plan, mynormpath, remove_path
from .watcher import Watcher
from .workflow import Workflow

__all__ = ("interpret_num_workers", "serve", "get_socket")


def main():
    asyncio.run(async_main())


async def async_main():
    args = parse_args()
    print(f"SOCKET {args.director_socket}", file=sys.stderr)
    print(f"PID {os.getpid()}", file=sys.stderr)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        num_workers = interpret_num_workers(args.num_workers)
        await reporter.set_num_workers(num_workers)
        await reporter("DIRECTOR", f"Listening on {args.director_socket}")
        try:
            await serve(
                Path(args.director_socket),
                num_workers,
                args.workflow,
                args.plan,
                reporter,
                args.show_perf,
                args.explain_rerun,
            )
        except Exception as exc:
            tbstr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            pages = [("Traceback", tbstr.strip())]
            await reporter("ERROR", "The director raised an exception.", pages)
            raise
        finally:
            await reporter("DIRECTOR", "See you!")
            await reporter.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stepup-director",
        description="Launch the director.",
    )
    parser.add_argument(
        "plan",
        default="plan.py",
        help="The top-level plan.py script (must be in current directory).",
    )
    parser.add_argument(
        "director_socket",
        help="The socket at which StepUp will listen for instructions.",
    )
    parser.add_argument(
        "--num-workers",
        "-w",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of workers running in parallel. "
        "When given as a real number with digits after the comma, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--workflow",
        "-f",
        default=".stepup/workflow.mpk.xz",
        help="The workflow database file. If it is present from a previous invocation of StepUp, "
        "file and step hashes are reused to determine if steps need to be re-executed or not. "
        "An up-to-date version will be saved each time when switching from run to watch phase.",
    )
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


def interpret_num_workers(num_workers: Decimal) -> int:
    """Convert the command-line argument num-workers into an integer."""
    if num_workers.as_tuple().exponent < 0:
        return int(len(os.sched_getaffinity(0)) * num_workers)
    else:
        return int(num_workers)


WORKFLOW_OH_NO = """
This is most likely a bug.
Please open an issue at https://github.com/reproducible-reporting/stepup-core/issues
Copy paste the traceback below and explain how to reproduce the problem.
The broken workflow file was copied to {}.
"""


async def serve(
    director_socket_path: Path,
    num_workers: int,
    path_workflow: str,
    path_plan: str,
    reporter: ReporterClient,
    show_perf: bool,
    explain_rerun: bool,
):
    """Server program.

    Parameters
    ----------
    director_socket_path
        The socket to listen to for remote calls.
    num_workers
        The number of worker processes.
    path_workflow
        The path where the workflow file will be written to
        (and read from if there was a previous run).
    path_plan
        The initial `plan.py` file.
    reporter
        The reporter client for sending information back to
        the terminal user interface.
    show_perf
        Show performance details after each completed step.
    explain_rerun
        Let workers explain why steps with recording info cannot be skipped.
    """
    if num_workers < 1:
        raise ValueError(f"Number of workers must be strictly positive, got {num_workers}")

    # Process paths
    path_workflow = Path(path_workflow)
    check_plan(path_plan)

    # Initialize workflow
    workflow = None
    if path_workflow.exists():
        try:
            workflow = Workflow.from_file(path_workflow)
            workflow.check_consistency()
            workflow.dissolve(not explain_rerun)
            await reporter("WORKFLOW", f"Loaded from {path_workflow}")
        except Exception as exc:
            traceback_fmt = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            path_workflow_bug = path_workflow.parent / "workflow-bug.mpk"
            shutil.copy(path_workflow, path_workflow_bug)
            message = WORKFLOW_OH_NO.format(path_workflow_bug)
            if workflow is not None:
                path_graph_bug = path_workflow.parent / "graph-bug.txt"
                with open(path_graph_bug, "w") as fh:
                    fh.write(workflow.format_str())
                message += f"The graph is written in text format to {path_graph_bug}.\n"
            pages = [("Oh no!", message), ("Traceback", traceback_fmt)]
            await reporter("WORKFLOW", f"Exception while loading workflow {path_workflow}.", pages)
            workflow = None
    if workflow is None:
        dir_workflow = path_workflow.dirname()
        if len(dir_workflow) > 0 and dir_workflow != ".":
            dir_workflow.makedirs_p()
        workflow = Workflow.from_scratch()

    # Create components
    scheduler = Scheduler(workflow.job_queue, workflow.job_queue_changed)
    scheduler.num_workers = num_workers
    watcher = Watcher(workflow, reporter, workflow.dir_queue)
    runner = Runner(
        watcher,
        scheduler,
        workflow,
        path_workflow,
        reporter,
        director_socket_path,
        show_perf,
        explain_rerun,
    )
    stop_event = asyncio.Event()
    director_handler = DirectorHandler(scheduler, workflow, runner, watcher, path_plan, stop_event)
    director_handler.define_boot()

    # Start tasks and wait for them to complete
    watcher_loop = watcher.loop(stop_event)
    runner_loop = runner.loop(stop_event)
    rpc_director = serve_socket_rpc(director_handler, director_socket_path, stop_event)
    try:
        await asyncio.gather(watcher_loop, runner_loop, rpc_director)
    finally:
        await reporter("DIRECTOR", "Stopping workers.")
        await runner.stop_workers()
        director_socket_path.remove_p()


@attrs.define
class DirectorHandler:
    _scheduler: Scheduler = attrs.field()
    _workflow: Workflow = attrs.field()
    _runner: Runner = attrs.field()
    _watcher: Watcher = attrs.field()
    _path_plan: str = attrs.field()
    _stop_event: asyncio.Event = attrs.field()

    #
    # For building up the workflow
    #

    def define_boot(self):
        """Define the initial plan.py as static file and create a step for it."""
        if Path(self._path_plan).absolute().parent != Path.cwd():
            raise ValueError("The plan script must be in the current directory.")
        self.static("root:", [self._path_plan])
        self.step(
            "root:",
            f"./{self._path_plan}",
            [self._path_plan],
            [],
            [],
            [],
            "./",
            False,
            None,
            False,
        )

    @contextlib.contextmanager
    def _dissolve_if_graph_changed(self):
        """A context manager that will dissolve the workflow is errors were after making changes."""
        self._workflow.graph_changed = False
        try:
            yield
        except Exception:
            if self._workflow.graph_changed:
                self._scheduler.drain()
                self._runner.dissolve_after_dump = True
            raise

    @allow_rpc
    def static(self, creator_key: str, paths: list[str]):
        """Add a list of absolute static paths to the workflow.

        They are stored internal as paths relative to STEPUP_ROOT.
        """
        with self._dissolve_if_graph_changed():
            self._workflow.declare_static(creator_key, paths)

    @allow_rpc
    def nglob(self, creator_key: str, ngm_data: list, strings: list[str]):
        """Register a glob patterns to be watched."""
        with self._dissolve_if_graph_changed():
            ngm = NGlobMulti.structure(ngm_data, strings)
            self._workflow.register_nglob(creator_key, ngm)

    @allow_rpc
    def defer(self, creator_key: str, patterns: list[str]):
        """Register a deferred glob."""
        with self._dissolve_if_graph_changed():
            self._workflow.defer_glob(creator_key, patterns)

    @allow_rpc
    def step(
        self,
        creator_key: str,
        command: str,
        inp_paths: list[str],
        env_vars: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        workdir: str,
        optional: bool,
        pool: str | None,
        block: bool,
    ) -> str:
        """Create a step in the workflow.

        Notes
        -----
        This is an RPC wrapper for `Workflow.define_step` with a few additional sanity checks:
        - The pool must exist.
        - The working directory must exist.
        """
        # If the pool is unknown, raise an error
        if not self._scheduler.has_pool(pool):
            raise GraphError(f"Unknown pool name: {pool}")
        if not workdir.endswith(os.sep):
            raise GraphError(f"A working directory must end with a separator, got: {workdir}")
        with self._dissolve_if_graph_changed():
            return self._workflow.define_step(
                creator_key,
                command,
                inp_paths,
                env_vars,
                out_paths,
                vol_paths,
                workdir,
                optional,
                pool,
                block,
            )

    @allow_rpc
    def pool(self, name: str, size: int):
        """Define a pool with given name and size.

        Notes
        -----
        This is an RPC wrapper for `Scheduler.set_pool`.
        """
        self._scheduler.set_pool(name, size)

    @allow_rpc
    def amend(
        self,
        step_key: str,
        inp_paths: list[str],
        env_vars: set[str],
        out_paths: list[str],
        vol_paths: list[str],
    ) -> bool:
        """Amend a step.

        Notes
        -----
        This is an RPC wrapper for `Workflow.amend_step`.
        """
        with self._dissolve_if_graph_changed():
            return self._workflow.amend_step(step_key, inp_paths, env_vars, out_paths, vol_paths)

    #
    # For interactive use
    #

    @allow_rpc
    async def shutdown(self):
        """Shut down the director and worker processes."""
        self._stop_event.set()
        self._scheduler.drain()
        self._watcher.interrupt.set()

    @allow_rpc
    async def drain(self):
        """Do not start new steps and switch to the watch phase after the steps completed.

        Notes
        -----
        This RPC blocks until all running steps have completed.
        """
        self._scheduler.drain()
        await self._watcher.active.wait()

    @allow_rpc
    async def join(self):
        """Block until the runner completed all (runnable) steps and shut down."""
        await self._watcher.active.wait()
        await self.shutdown()

    @allow_rpc
    def graph(self, prefix: str):
        """Write out the graph in text format."""
        with open(f"{prefix}.txt", "w") as fh:
            print(self._workflow.format_str(), file=fh)
        with open(f"{prefix}_creator.dot", "w") as fh:
            print(self._workflow.format_dot_creator(), file=fh)
        with open(f"{prefix}_supplier.dot", "w") as fh:
            print(self._workflow.format_dot_supplier(), file=fh)

    @allow_rpc
    def from_scratch(self):
        """Remove all recordings and run everything again.

        Notes
        -----
        This has no effect during the run phase.
        """
        if not self._watcher.active.is_set():
            return
        self._workflow.discard_recordings()
        self.try_replay()

    @allow_rpc
    def try_replay(self):
        """Make all steps pending and try replaying them. When needed, steps are rerun instead.

        Notes
        -----
        This has no effect during the run phase.
        """
        if not self._watcher.active.is_set():
            return
        self._workflow.dissolve()
        self.define_boot()
        self.run()

    @allow_rpc
    def run(self):
        """Run pending steps (based on file changes observed in the watch phase).

        Notes
        -----
        This has no effect during the run phase.
        """
        if not self._watcher.active.is_set():
            return
        self._watcher.interrupt.set()
        self._scheduler.resume()
        self._runner.resume.set()

    @allow_rpc
    async def cleanup(self, paths: list[str]) -> tuple[int, int]:
        """Recursively clean up outputs (consumer files and directories).

        Parameters
        ----------
        paths
            A list of paths to consider for removal.

        Returns
        -------
        numf
            The number of files effectively removed.
        numd
            The number of directories effectively removed.
        """
        if not self._watcher.active.is_set():
            raise ValueError("Cleanup is only allowed in the watch phase.")
        initial_keys = []
        for path in paths:
            key = f"file:{path}"
            if key not in self._workflow.nodes:
                raise ValueError(f"Path not known to workflow: {path}")
            initial_keys.append(key)
        visited = set()
        for key in initial_keys:
            self._workflow.walk_consumers(key, visited)

        numf = 0
        numd = 0
        for key in sorted(visited, reverse=True):
            if key.startswith("file:"):
                file = self._workflow.get_file(key)
                if file.get_state(self._workflow) not in (FileState.STATIC, FileState.MISSING):
                    remove_path(file.path)
                    if file.path.endswith("/"):
                        numd += 1
                    else:
                        numf += 1
        return numf, numd

    @allow_rpc
    async def watch_update(self, path: str):
        """Block until the watcher observed an update of the file."""
        path = mynormpath(path)
        await self._watcher.active.wait()
        while True:
            if path in self._watcher.updated:
                return
            self._watcher.files_changed.clear()
            await self._watcher.files_changed.wait()

    @allow_rpc
    async def watch_delete(self, path: str):
        """Block until the watcher observed the deletion of the file."""
        path = mynormpath(path)
        await self._watcher.active.wait()
        while True:
            if path in self._watcher.deleted:
                return
            self._watcher.files_changed.clear()
            await self._watcher.files_changed.wait()

    @allow_rpc
    async def wait(self):
        """Block until the runner completed all (runnable) steps."""
        await self._watcher.active.wait()


def get_socket() -> str:
    """Block until the director socket is known and return it."""
    stepup_root = Path(os.getenv("STEPUP_ROOT", "./"))
    path_director_log = stepup_root / ".stepup/logs/director"
    secs = 0
    while True:
        time.sleep(secs)
        if os.path.isfile(path_director_log):
            with open(path_director_log) as fh:
                line = fh.readline()
                if line.startswith("SOCKET"):
                    path_socket = Path(line[6:].strip())
                    if len(path_socket) > 2 and path_socket.exists():
                        return path_socket
                    else:
                        message = (
                            f"Socket {path_socket} read from {path_director_log} does not exist. "
                            "Stepup not running?"
                        )
                else:
                    message = f"File {path_director_log} does not start with SOCKET line."
        else:
            message = f"File {path_director_log} not found."
        if secs == 0.0:
            print("Trying to contact StepUp director process.", file=sys.stderr)
        secs += 0.1
        print(f"{message}  Waiting {secs:.1f} seconds.", file=sys.stderr)


if __name__ == "__main__":
    main()
