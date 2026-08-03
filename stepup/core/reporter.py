# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Terminal output of StepUp's builder progress and observed file changes."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Iterable
from time import perf_counter

import attrs
from path import Path
from rich.console import Console, RenderableType
from rich.markup import escape as escape_markup
from rich.progress import BarColumn, MofNCompleteColumn, TaskID, TextColumn
from rich.progress import Progress as ProgressBar
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme

from .constants import FAIL_LOG, SUCCESS_LOG, WARNING_LOG
from .rpc import AsyncRPCClient, BaseAsyncRPCClient, DummyAsyncRPCClient, allow_rpc
from .utils import escape_command_display

logger = logging.getLogger(__name__)

__all__ = ("PROGRESS_REFRESH_DELAY", "ReporterClient", "ReporterHandler")


PROGRESS_REFRESH_DELAY = 0.3
PROGRESS_REFRESH_INTERVAL = 1.0
ACTION_COLORS = {
    # blue
    "START": "blue",
    # red
    "ERROR": "red",
    "FAIL": "red",
    # green
    "SUCCESS": "green",
    # yellow
    "WARNING": "yellow",
    # cyan
    "DELETED": "cyan",
    "DETACHED": "cyan",
    "DROPAMEND": "cyan",
    "NOSKIP": "cyan",
    "DEFERRED": "cyan",
    "REMOVE": "cyan",
    "SKIP": "cyan",
    "UNCHANGED": "cyan",
    "UPDATED": "cyan",
    # magenta
    "DIRECTOR": "magenta",
    "KEYBOARD": "magenta",
    "STARTUP": "magenta",
    # (default)
    "PHASE": "",
}


@attrs.define
class ReporterClient:
    socket_path: Path | None = attrs.field(default=None)
    """Path to the Unix socket of the TUI to connect to, or `None` for a dummy client."""

    client: BaseAsyncRPCClient = attrs.field(factory=DummyAsyncRPCClient)
    """The RPC client to use for reporting, or a dummy client if `socket_path` is `None`."""

    _start_job_buffer: dict[int, tuple[str, str]] = attrs.field(init=False, factory=dict)
    """Buffered `start_job` signals not yet sent, keyed by `job_i`.

    A `job_i` present here means its start was not yet flushed to the server.
    """

    _stop_job_buffer: set[int] = attrs.field(init=False, factory=set)
    """Buffered `stop_job` signals not yet sent.

    Only holds `job_i`s whose matching start was already flushed in an earlier batch:
    a start/stop pair arriving within the same delay window is dropped instead
    (see `stop_job`), since it would never be visible in the progress bar anyway.
    """

    _flush_jobs_handle: asyncio.TimerHandle | None = attrs.field(init=False, default=None)
    """Handle for the scheduled `_flush_jobs` call, or `None` when none is pending.

    Mirrors `StepUpProgressBar._refresh_handle`'s coalescing pattern.
    """

    _flush_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """In-flight `_flush_jobs` tasks, kept alive here so they cannot be garbage-collected
    mid-send (same rationale as the `tasks` set in `rpc.py::_serve_rpc_recv_loop`)."""

    @classmethod
    @contextlib.asynccontextmanager
    async def socket(cls, path: Path | None) -> AsyncGenerator["ReporterClient", None]:
        if path is None:
            yield cls(path, DummyAsyncRPCClient())
        else:
            async with await AsyncRPCClient.socket(path) as client:
                yield cls(path, client)

    async def __call__(
        self, action: str, description: str, pages: list[tuple[str, str]] | None = None
    ):
        if self.client is not None:
            if pages is None:
                pages = []
            await self.client.call.report(action, description, pages)

    async def set_njob(self, njob: int):
        if self.client is not None:
            await self.client.call.set_njob(njob)

    def start_job(self, letter: str, description: str, job_i: int):
        """Buffer a job-start signal, sent later in a batched `update_jobs` RPC call."""
        self._start_job_buffer[job_i] = (letter, description)
        self._request_jobs_flush()

    def stop_job(self, job_i: int):
        """Buffer a job-stop signal, sent later in a batched `update_jobs` RPC call.

        If the matching start is still buffered (started and stopped within the same
        delay window), drop both: the job never needs to appear in the progress bar.
        """
        if self._start_job_buffer.pop(job_i, None) is None:
            self._stop_job_buffer.add(job_i)
        self._request_jobs_flush()

    def _request_jobs_flush(self):
        """Schedule `_flush_jobs`, coalescing with any flush already pending."""
        if self._flush_jobs_handle is None:
            loop = asyncio.get_running_loop()
            self._flush_jobs_handle = loop.call_later(
                PROGRESS_REFRESH_DELAY, self._on_flush_jobs_timer
            )

    def _on_flush_jobs_timer(self):
        self._flush_jobs_handle = None
        task = asyncio.get_running_loop().create_task(self._flush_jobs())
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)

    async def _flush_jobs(self):
        """Send buffered start/stop job signals in a single batched RPC call."""
        if len(self._start_job_buffer) == 0 and len(self._stop_job_buffer) == 0:
            return
        starts, self._start_job_buffer = self._start_job_buffer, {}
        stops, self._stop_job_buffer = self._stop_job_buffer, set()
        if self.client is not None:
            await self.client.call.update_jobs(starts, stops)

    async def update_counts(self, nsuccess: int, ntotal: int):
        if self.client is not None:
            await self.client.call.update_counts(nsuccess, ntotal)

    async def check_logs(self):
        if self.client is not None:
            await self.client.call.check_logs()

    async def shutdown(self):
        if self.client is not None:
            await self.client.call.shutdown()

    async def close(self):
        if self._flush_jobs_handle is not None:
            self._flush_jobs_handle.cancel()
            self._flush_jobs_handle = None
        await self._flush_jobs()
        if len(self._flush_tasks) > 0:
            await asyncio.gather(*self._flush_tasks)
        if self.client is not None:
            try:
                await self.client.close()
            except ConnectionError as exc:
                logger.warning("Ignoring exception when closing reporter client: %r", exc)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        await self.close()


class StepUpProgressBar(ProgressBar):
    """Custom progress bar to handle the case where the console is not a terminal."""

    def __init__(self, *args, **kwargs):
        self._njob: int = 0
        self._running: dict[int, tuple[float, str, str]] = {}
        self._refresh_handle: asyncio.TimerHandle | None = None
        super().__init__(*args, **kwargs)

    def request_refresh(self, delay: float = PROGRESS_REFRESH_DELAY):
        """Schedule a refresh, coalescing with any refresh already pending.

        This coalesces bursts of progress-relevant events into a single delayed `refresh()` call,
        instead of repainting the terminal on every event.
        It is also used by `do_refresh` to keep ticking at `PROGRESS_REFRESH_INTERVAL`
        while steps are running, so their elapsed times stay current on screen.
        """
        if self._refresh_handle is None:
            loop = asyncio.get_running_loop()
            self._refresh_handle = loop.call_later(delay, self.do_refresh)

    def do_refresh(self):
        """Perform an immediate refresh of the progress bar.

        This is called by `request_refresh` after the coalescing delay,
        or by functions that need an immediate refresh outside that delay.
        """
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None
        self.refresh()
        if self._running:
            # Keep ticking while steps are running, to update elapsed times.
            self.request_refresh(PROGRESS_REFRESH_INTERVAL)

    def set_njob(self, njob: int):
        """Set the number of jobs in the progress bar."""
        self._njob = njob
        self.request_refresh()

    def update_jobs(self, now: float, starts: dict[int, tuple[str, str]], stops: set[int]):
        """Apply a batch of job start/stop signals to the progress bar."""
        for job_i, (letter, description) in starts.items():
            self._running[job_i] = (now, letter, description)
        for job_i in stops:
            self._running.pop(job_i, None)
        # No need to coalesce here, since this is already called from a coalesced `_flush_jobs`.
        self.do_refresh()

    def get_renderables(self) -> Iterable[RenderableType]:
        if len(self._running) > 0:
            running = sorted(self._running.values())[: self.console.height // 2 - 1]
            rule_message = f"Running steps {len(self._running)}/{self._njob}"
            if len(running) < len(self._running):
                rule_message += f" ({len(running)} shown)"
            yield Rule(rule_message, style="bold")
            for start, letter, description in running:
                elapsed = perf_counter() - start
                text = Text(
                    no_wrap=True,
                    overflow="crop",
                )
                text.append(f"{elapsed:7.0f} ", "bold gray50")
                text.append(f"{letter} ", "bold gray42")
                text.append(f"│ {description}")
                yield text
        yield from super().get_renderables()


@attrs.define
class ReporterHandler:
    show_progress: bool = attrs.field(default=True)
    """Whether the user asked for progress information at all (the `--progress` option)."""

    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set by `shutdown`, ending the RPC server that serves this handler."""

    console: Console = attrs.field(init=False)
    """The rich console that all output goes through."""

    live_progress: bool = attrs.field(init=False)
    """Whether progress can be shown live, i.e. wanted **and** possible.

    This is the single decision behind the progress bar: `progress_bar` and `task_id_step`
    exist iff this is true. `tui.py` also forwards it to the director (`--live-progress`),
    so the director does not send updates that would be dropped here anyway.
    """

    progress_bar: StepUpProgressBar | None = attrs.field(init=False)
    """The progress bar, or `None` when progress cannot be shown live."""

    task_id_step: TaskID | None = attrs.field(init=False)
    """The progress bar task tracking completed steps, or `None` without a progress bar."""

    start: float = attrs.field(init=False, factory=perf_counter)
    """The moment this handler was created, i.e. roughly the start of the build."""

    _first_build_phase: bool = attrs.field(init=False, default=True)
    """Whether the next `PHASE build` report is the first one of this director's lifetime.

    The log files are already fresh at that point: `tui.py`'s `_reset_stepup_dir` clears
    them before the director is even spawned. Skipping the wipe on this first occurrence
    preserves `STARTUP`-phase errors (e.g. a file that could not be hashed), which would
    otherwise be reported to `fail.log` and then immediately erased before the first
    `job_loop` runs.
    """

    @console.default
    def _default_console(self):
        theme = Theme(
            {
                "rule.line": "bold gray50",
                "bar.complete": "bold white",
                "bar.finished": "bold white",
            }
        )
        return Console(highlight=False, theme=theme)

    @live_progress.default
    def _default_live_progress(self):
        return self.show_progress and self.console.is_terminal

    @progress_bar.default
    def _default_progress_bar(self):
        if not self.live_progress:
            return None
        progress_bar = StepUpProgressBar(
            TextColumn("{task.description}"),
            BarColumn(None),
            MofNCompleteColumn(),
            transient=True,
            console=self.console,
            auto_refresh=False,
        )
        progress_bar.start()
        return progress_bar

    @task_id_step.default
    def _default_task_id_step(self):
        return self.progress_bar.add_task("", total=0, visible=True) if self.live_progress else None

    @allow_rpc
    def report(self, action: str, description: str, pages: list[tuple[str, str]]):
        # Action info
        action_color = ACTION_COLORS[action]

        # Print action with extra info
        description = escape_markup(description)
        line = f"[bold {action_color}]{action:>9s}[/] │ "
        if action == "START":
            line += description
        else:
            line += f"[gray50]{description}[/]"
        self.console.print(
            line, no_wrap=self.console.is_terminal, soft_wrap=not self.console.is_terminal
        )

        # Pages if any
        for title, page in pages:
            self.console.rule(f"[white]{title}[/]")
            self.console.print(f"[gray50]{escape_markup(page)}[/]", soft_wrap=True)
        if len(pages) > 0:
            self.console.rule()

        # File logging
        if action == "PHASE" and description == "build":
            if self._first_build_phase:
                # Skip the wipe on the very first build phase: see `_first_build_phase`.
                self._first_build_phase = False
            else:
                # Delete the log files at the start of a new build phase.
                for path_log in [FAIL_LOG, WARNING_LOG, SUCCESS_LOG]:
                    path_log.remove_p()
        path_log = {
            "red": FAIL_LOG,
            "yellow": WARNING_LOG,
        }.get(action_color, SUCCESS_LOG)
        path_log.parent.makedirs_p()
        with open(path_log, "a") as file:
            console = Console(file=file, width=80)
            console.print(line, no_wrap=True, soft_wrap=True)
            for title, page in pages:
                console.rule(title)
                console.print(page, soft_wrap=True)
            if len(pages) > 0:
                console.rule()

    @allow_rpc
    def set_njob(self, njob: int):
        """Set the number of jobs in the progress bar."""
        if self.progress_bar is not None:
            self.progress_bar.set_njob(njob)

    @allow_rpc
    def update_jobs(self, starts: dict[int, tuple[str, str]], stops: set[int]):
        if self.progress_bar is not None:
            starts = {
                job_i: (letter, escape_command_display(description))
                for job_i, (letter, description) in starts.items()
            }
            self.progress_bar.update_jobs(perf_counter(), starts, stops)

    @allow_rpc
    def update_counts(self, nsuccess: int, ntotal: int):
        if self.progress_bar is not None:
            self.progress_bar.update(
                self.task_id_step,
                completed=nsuccess,
                total=ntotal,
            )
            # The caller of update_counts is expected to coalesce multiple calls,
            # as it also increases efficiency on the caller's side.
            self.progress_bar.do_refresh()

    @allow_rpc
    def check_logs(self):
        """Check for the presence of fail/warning logs and report them."""
        paths_log = [path_log for path_log in [FAIL_LOG, WARNING_LOG] if path_log.exists()]
        if len(paths_log) > 0:
            self.report("WARNING", "Check logs: {}".format(" ".join(paths_log)), [])

    @allow_rpc
    def shutdown(self):
        if self.progress_bar is not None:
            self.progress_bar.stop()
        self.stop_event.set()
