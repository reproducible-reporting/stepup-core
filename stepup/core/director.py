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
"""director."""

import asyncio
import contextlib
import os
import argparse
import shutil
import sys
import traceback
from decimal import Decimal

import attrs
from path import Path

from stepup.core.nglob import NGlobMulti
from .rpc import serve_socket_rpc, allow_rpc
from .workflow import Workflow
from .exceptions import GraphError
from .reporter import ReporterClient
from .runner import Runner, Phase
from .scheduler import Scheduler
from .utils import check_plan, mynormpath
from .watcher import Watcher


__all__ = ("interpret_num_workers", "serve")


def main():
    asyncio.run(async_main())


async def async_main():
    args = parse_args()
    print(f"PID {os.getpid()}", file=sys.stderr)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        num_workers = interpret_num_workers(args.num_workers)
        await reporter.set_num_workers(num_workers)
        await reporter("DIRECTOR", f"Listening on {args.director_socket}")
        try:
            await serve(
                args.director_socket,
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
    director_socket_path: str,
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

    # Initialize components
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
    scheduler = Scheduler(workflow.queue, workflow.queue_changed)
    scheduler.num_workers = num_workers
    runner = Runner(
        scheduler, workflow, path_workflow, reporter, director_socket_path, show_perf, explain_rerun
    )
    watcher = Watcher(workflow, reporter)

    # Start tasks and wait for them to complete
    stop_event = asyncio.Event()
    cycle = asyncio.create_task(cycle_run_watch(runner, watcher, stop_event), name="run-watch loop")
    director_handler = DirectorHandler(scheduler, workflow, runner, watcher, path_plan, stop_event)

    # Add the initial plan
    director_handler.define_boot()

    # Start RPC task
    rpc_director = asyncio.create_task(
        serve_socket_rpc(director_handler, director_socket_path, stop_event), name="director-rpc"
    )
    try:
        await asyncio.gather(cycle, rpc_director)
    finally:
        await reporter("DIRECTOR", "Stopping workers.")
        await runner.stop_workers()


async def cycle_run_watch(runner: Runner, watcher: Watcher, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await runner.loop()
        await watcher.loop()


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
        if self._runner.phase.is_set(Phase.WATCH):
            raise GraphError("A static file cannot be declared in watch phase.")
        with self._dissolve_if_graph_changed():
            self._workflow.declare_static(creator_key, paths)

    @allow_rpc
    def nglob(self, creator_key: str, ngm_data: list, strings: list[str]):
        """Register a number of glob patterns to be watched."""
        with self._dissolve_if_graph_changed():
            ngm = NGlobMulti.structure(ngm_data, strings)
            self._workflow.register_nglob(creator_key, ngm)

    @allow_rpc
    def defer(self, creator_key: str, patterns: list[str]):
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
        """See ``stepup.core.api.step``."""
        # If the pool is unknown, raise an error
        if self._runner.phase.is_set(Phase.WATCH):
            raise GraphError(f"A step cannot be defined in watch phase: {command}")
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
        """See ``stepup.core.api.pool``."""
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
        """See ``stepup.core.api.amend``."""
        if self._runner.phase.is_set(Phase.WATCH):
            raise GraphError(f"A step cannot be amended in watch phase: {step_key}")
        with self._dissolve_if_graph_changed():
            return self._workflow.amend_step(step_key, inp_paths, env_vars, out_paths, vol_paths)

    #
    # For interactive use
    #

    @allow_rpc
    async def shutdown(self):
        """See ``stepup.core.api.shutdown``."""
        self._scheduler.drain()
        self._watcher.interrupt.set()
        self._stop_event.set()

    @allow_rpc
    async def drain(self):
        """See ``stepup.core.api.shutdown``."""
        self._scheduler.drain()
        await self._runner.phase.wait(Phase.WATCH)

    @allow_rpc
    async def join(self):
        """See ``stepup.core.api.join``."""
        await self._runner.phase.wait(Phase.WATCH)
        await self.shutdown()

    @allow_rpc
    async def run(self):
        """See ``stepup.core.api.run``."""
        # No point in starting run phase when the runner is active.
        if self._runner.phase.is_set(Phase.RUN):
            return
        self._watcher.interrupt.set()
        # Only return after the runner loop has started.
        self._scheduler.resume()
        await self._runner.phase.wait(Phase.RUN)

    @allow_rpc
    def graph(self, path_graph: str):
        """Write out the graph in text format."""
        with open(path_graph, "w") as fh:
            print(self._workflow.format_str(), file=fh)

    @allow_rpc
    async def from_scratch(self):
        """Remove all recordings and run everything again."""
        # No point in starting run phase when the runner is active.
        if self._runner.phase.is_set(Phase.RUN):
            return
        self._workflow.discard_recordings()
        await self.try_replay()

    @allow_rpc
    async def try_replay(self):
        """Make all steps pending and try replaying them. When needed, steps are rerun instead."""
        # No point in starting run phase when the runner is active.
        if self._runner.phase.is_set(Phase.RUN):
            return
        self._workflow.dissolve()
        self._watcher.interrupt.set()
        # This is a bit hacky: make runner active to be able to modify the workflow,
        # which is normally only done in run phase.
        self._runner.phase.set(Phase.RUN)
        self.define_boot()
        self._runner.phase.set(Phase.WATCH)
        self._scheduler.resume()
        await self._runner.phase.wait(Phase.RUN)

    @allow_rpc
    async def watch_add(self, path: str):
        """Wait for a file to be added by the watcher."""
        path = mynormpath(path)
        while not self._watcher.interrupt.is_set():
            if path in self._watcher.added:
                return
            self._watcher.changed.clear()
            await self._watcher.changed.wait()

    @allow_rpc
    async def watch_del(self, path: str):
        """Wait for a file to be deleted by the watcher."""
        path = mynormpath(path)
        while not self._watcher.interrupt.is_set():
            if path in self._watcher.deleted:
                return
            self._watcher.changed.clear()
            await self._watcher.changed.wait()

    @allow_rpc
    async def wait(self):
        """See ``stepup.core.api.wait``."""
        await self._runner.phase.wait(Phase.WATCH)


if __name__ == "__main__":
    main()
