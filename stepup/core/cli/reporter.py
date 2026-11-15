# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Terminal output of StepUp's builder progress and observed file changes."""

import asyncio
import logging
import sys
from collections.abc import Iterable
from time import perf_counter

import attrs
from rich.console import Console, RenderableType
from rich.markup import escape as escape_markup
from rich.progress import BarColumn, MofNCompleteColumn, TaskID, TextColumn
from rich.progress import Progress as ProgressBar
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme

from ..constants import FAIL_LOG, PROGRESS_REFRESH_DELAY, SUCCESS_LOG, WARNING_LOG
from ..platform.stdio import rich_legacy_windows
from ..rpc.server import allow_rpc
from ..utils import escape_control_chars

__all__ = ("ReporterHandler",)

logger = logging.getLogger(__name__)


PROGRESS_REFRESH_INTERVAL = 1.0

TAG_COLORS = {
    # Neutral
    "START": "blue",
    # Problems
    "ERROR": "red",
    "FAIL": "red",
    # Potential problems
    "WARNING": "yellow",
    # Successes
    "SUCCESS": "green",
    # Workflow-related details
    "DELETED": "cyan",
    "UNAMEND": "cyan",
    "NOSKIP": "cyan",
    "DEFERRED": "cyan",
    "REMOVE": "cyan",
    "SKIP": "cyan",
    "SAMEHASH": "cyan",
    "UPDATED": "cyan",
    # Events outside the workflow
    "DIRECTOR": "magenta",
    "KEYBOARD": "magenta",
    "STARTUP": "magenta",
    # Separation between phases
    "PHASE": "",  # default color
}


class StepUpProgressBar(ProgressBar):
    """Progress bar that also lists the steps currently running, with coalesced refreshes."""

    def __init__(self, *args, **kwargs):
        self._njob: int = 0
        self._running: dict[int, tuple[float, str, str]] = {}
        self._refresh_handle: asyncio.TimerHandle | None = None
        super().__init__(*args, **kwargs)

    def request_refresh(self, delay: float = PROGRESS_REFRESH_DELAY):
        """Schedule a refresh, coalescing with any refresh already pending.

        Coalesces bursts of progress-relevant events into a single delayed `refresh()` call,
        instead of repainting the terminal on every event.
        """
        if self._refresh_handle is None:
            loop = asyncio.get_running_loop()
            self._refresh_handle = loop.call_later(delay, self.refresh_now)

    def refresh_now(self):
        """Perform an immediate refresh of the progress bar.

        Bypasses any pending coalesced delay.
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

    def update_running_jobs(
        self, started: dict[int, tuple[str, str]], stopped: set[int], now: float
    ):
        """Apply a batch of job start/stop signals to the progress bar."""
        for job_i, (letter, description) in started.items():
            self._running[job_i] = (now, letter, description)
        for job_i in stopped:
            self._running.pop(job_i, None)
        # The batch of signals is already coalesced, so refresh immediately.
        self.refresh_now()

    def discount_suspension(self, seconds: float) -> None:
        """Move the start of every running job forward, to discount a suspension.

        The elapsed time shown per job is a wall-clock difference,
        which keeps growing while the steps are stopped.
        Shifting their starts keeps the display consistent
        with the wall time recorded for the steps (see `Executor.suspended_total`).
        """
        self._running = {
            job_i: (start + seconds, letter, description)
            for job_i, (start, letter, description) in self._running.items()
        }

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
                text.append(f"{elapsed:6.0f} ", "bold gray50")
                text.append(f"{letter} ", "bold gray42")
                text.append(f"│ {description}")
                yield text
        yield from super().get_renderables()


@attrs.define
class ReporterHandler:
    show_progress: bool = attrs.field(default=True)
    """Whether the user asked for progress information at all (the `--progress` option)."""

    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set by `stop_reporting`, ending the RPC server that serves this handler."""

    console: Console = attrs.field(init=False)
    """The rich console that all output goes through."""

    live_progress: bool = attrs.field(init=False)
    """Whether progress can be shown live, i.e. wanted **and** possible.

    This is the single decision behind the progress bar:
    `progress_bar` and `task_id_step` exist iff this is true.
    `build.py` also forwards it to the director (`--live-progress`),
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

    The log files are already fresh at that point:
    `build.py`'s `_reset_stepup_dir` clears them before the director is even spawned.
    Skipping the wipe on this first occurrence preserves `STARTUP`-phase errors
    (e.g. a file that could not be hashed), which would otherwise be reported to `fail.log`
    and then immediately erased before the first `job_loop` runs.
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
        return Console(highlight=False, theme=theme, legacy_windows=rich_legacy_windows(sys.stdout))

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
    def report(self, tag: str, description: str, pages: list[tuple[str, str]]):
        # Print the tag with extra info
        tag_color = TAG_COLORS[tag]
        description = escape_markup(description)
        line = f"[bold {tag_color}]{tag:>8s}[/] │ "
        if tag == "START":
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
        if tag == "PHASE" and description == "build":
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
        }.get(tag_color, SUCCESS_LOG)
        path_log.parent.makedirs_p()
        with open(path_log, "a", encoding="utf-8", newline="\n") as file:
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
    def update_running_jobs(self, started: dict[int, tuple[str, str]], stopped: set[int]):
        if self.progress_bar is not None:
            started = {
                job_i: (letter, escape_control_chars(description))
                for job_i, (letter, description) in started.items()
            }
            self.progress_bar.update_running_jobs(started, stopped, perf_counter())

    @allow_rpc
    def update_progress(self, ndone: int, ntotal: int):
        if self.progress_bar is not None:
            self.progress_bar.update(
                self.task_id_step,
                completed=ndone,
                total=ntotal,
            )
            # Callers of `update_progress` are expected to coalesce their calls,
            # which also saves work on their side.
            self.progress_bar.refresh_now()

    @allow_rpc
    def warn_about_logs(self):
        """Check for the presence of fail/warning logs and report them."""
        paths_log = [path_log for path_log in [FAIL_LOG, WARNING_LOG] if path_log.exists()]
        if len(paths_log) > 0:
            self.report("WARNING", "Check logs: {}".format(" ".join(paths_log)), [])

    def suspend_display(self) -> None:
        """Stop the live display, because this process is about to be suspended.

        Stopping it erases the progress bar and makes the cursor visible again,
        so the shell prompt does not appear in a terminal StepUp has left half-decorated.
        """
        if self.progress_bar is not None:
            self.progress_bar.stop()

    def resume_display(self, suspended_seconds: float = 0.0) -> None:
        """Restart the live display stopped by `suspend_display` and repaint it.

        Parameters
        ----------
        suspended_seconds
            The wall time spent suspended, which the running steps did not spend working
            and which is therefore taken off their elapsed times.
        """
        if self.progress_bar is not None and not self.stop_event.is_set():
            self.progress_bar.discount_suspension(suspended_seconds)
            self.progress_bar.start()
            self.progress_bar.refresh_now()

    @allow_rpc
    def stop_reporting(self):
        """Stop the live display and end the RPC server that serves this handler."""
        if self.progress_bar is not None:
            self.progress_bar.stop()
        self.stop_event.set()
