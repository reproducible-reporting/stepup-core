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
"""Terminal output of StepUp's runner progress and observed file changes."""

import asyncio
import contextlib
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

from .enums import StepState
from .rpc import AsyncRPCClient, BaseAsyncRPCClient, DummyAsyncRPCClient, allow_rpc

__all__ = ("ReporterClient", "ReporterHandler")


@attrs.define
class ReporterClient:
    socket_path: Path | None = attrs.field(default=None)
    client: BaseAsyncRPCClient = attrs.field(factory=DummyAsyncRPCClient)

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
                pages = {}
            await self.client.call.report(action, description, pages)

    async def set_num_workers(self, num_workers: int):
        if self.client is not None:
            await self.client.call.set_num_workers(num_workers)

    async def start_step(self, description: str, step_i: int):
        if self.client is not None:
            await self.client.call.start_step(description, step_i)

    async def stop_step(self, step_i: int):
        if self.client is not None:
            await self.client.call.stop_step(step_i)

    async def update_step_counts(self, step_counter: dict[StepState, int]):
        if self.client is not None:
            await self.client.call.update_step_counts(step_counter)

    async def check_logs(self):
        if self.client is not None:
            await self.client.call.check_logs()

    async def shutdown(self):
        if self.client is not None:
            await self.client.call.shutdown()

    async def close(self):
        if self.client is not None:
            await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        await self.close()


class StepUpProgressBar(ProgressBar):
    """Custom progress bar to handle the case where the console is not a terminal."""

    def __init__(self, *args, **kwargs):
        self._num_workers = 0
        self._running = {}
        super().__init__(*args, **kwargs)

    def set_num_workers(self, num_workers: int):
        """Set the number of workers in the progress bar."""
        self._num_workers = num_workers

    def start_step(self, start: float, description: str, step_i: int):
        """Start a step in the progress bar."""
        self._running[step_i] = (start, description)

    def stop_step(self, step_i: int):
        """Stop a step in the progress bar."""
        self._running.pop(step_i, None)

    def get_renderables(self) -> Iterable[RenderableType]:
        if len(self._running) > 0:
            running = sorted(self._running.values())[: self.console.height // 2 - 1]
            rule_message = f"Active workers {len(self._running)}/{self._num_workers}"
            if len(running) < len(self._running):
                rule_message += f" ({len(running)} shown)"
            yield Rule(rule_message, style="bold")
            for start, description in running:
                elapsed = perf_counter() - start
                text = Text(
                    no_wrap=True,
                    overflow="crop",
                )
                text.append(f"{elapsed:10.0f}", "bold gray50")
                text.append(f" │ {description}")
                yield text
        yield from super().get_renderables()


@attrs.define
class ReporterHandler:
    show_perf: bool = attrs.field(default=False)
    show_progress: bool = attrs.field(default=True)
    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    _step_counts: dict[StepState, int] = attrs.field(init=False, factory=dict)
    _num_digits: int = attrs.field(init=False, default=3)
    console: Console = attrs.field(init=False)
    progress_bar: ProgressBar | None = attrs.field(init=False)
    task_id_step: TaskID | None = attrs.field(init=False)
    start: float = attrs.field(init=False, factory=perf_counter)

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

    @progress_bar.default
    def _default_progress_bar(self):
        if not (self.show_progress and self.console.is_terminal):
            return None
        progress_bar = StepUpProgressBar(
            TextColumn("{task.description}"),
            BarColumn(None),
            MofNCompleteColumn(),
            transient=True,
            console=self.console,
            auto_refresh=True,
            refresh_per_second=2,
        )
        progress_bar.start()
        return progress_bar

    @task_id_step.default
    def _default_task_id_step(self):
        return (
            self.progress_bar.add_task("✔ ", total=0, visible=True)
            if self.show_progress and self.console.is_terminal
            else None
        )

    @allow_rpc
    def report(self, action: str, description: str, pages: list[tuple[str, str]]):
        if self.show_progress:
            # Progress bar
            nsuc = self._step_counts.get(StepState.SUCCEEDED, 0)
            nrun = self._step_counts.get(StepState.RUNNING, 0)
            npen = self._step_counts.get(StepState.PENDING, 0) + self._step_counts.get(
                StepState.QUEUED, 0
            )
            nd = max(self._num_digits, len(str(nsuc)), len(str(nrun)), len(str(npen)))
            self._num_digits = nd
            if self.console.is_terminal:
                self.progress_bar.update(
                    self.task_id_step,
                    completed=nsuc,
                    total=nsuc + nrun + npen,
                )

        # Action info
        action_color = {
            "START": "blue",
            "FAIL": "red",
            "ERROR": "red",
            "SUCCESS": "green",
            "DELETED": "yellow",
            "UPDATED": "yellow",
            "SKIP": "cyan",
            "NOSKIP": "yellow",
            "RESCHEDULE": "yellow",
            "DROPAMEND": "yellow",
            "WARNING": "yellow",
            "UNCHANGED": "cyan",
            "PHASE": "",
        }.get(action, "magenta")

        # Print action with extra info
        description = escape_markup(description)
        line = f"[bold {action_color}]{action:>10s}[/] │ "
        if action == "START":
            line += description
        else:
            line += f"[gray50]{description}[/]"
        if self.show_perf:
            now = perf_counter()
            line = f"[gray50]{perf_counter() - self.start:7.2f} {nrun:{nd}d} [/]" + line
            if action == "PHASE":
                self.start = now
        if not self.console.is_terminal and self.show_progress:
            # If not a terminal, the progress bars are not shown,
            # so we need to print the completed and total number of steps.
            progress = f"{nsuc}/{nsuc + nrun + npen}"
            line = f"[gray50]{progress:>11s} | [/]" + line
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
        if action == "PHASE" and description == "run":
            # Delete the log files when rerunning the build.
            for path_log in [".stepup/fail.log", ".stepup/warning.log", ".stepup/success.log"]:
                Path(path_log).remove_p()
        path_log = Path(
            {
                "red": ".stepup/fail.log",
                "yellow": ".stepup/warning.log",
            }.get(action_color, ".stepup/success.log")
        )
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
    def set_num_workers(self, num_workers: int):
        """Set the number of workers in the progress bar."""
        if self.progress_bar is not None:
            self.progress_bar.set_num_workers(num_workers)

    @allow_rpc
    def start_step(self, description: str, step_i: int):
        if self.progress_bar is not None:
            self.progress_bar.start_step(perf_counter(), description, step_i)
            self.progress_bar.refresh()

    @allow_rpc
    def stop_step(self, step_i: int):
        if self.progress_bar is not None:
            self.progress_bar.stop_step(step_i)

    @allow_rpc
    def update_step_counts(self, step_counts: dict[StepState, int]):
        self._step_counts = step_counts

    @allow_rpc
    def check_logs(self):
        """Check for the presence of fail/warning logs and report them."""
        paths_log = [
            path_log
            for path_log in [".stepup/fail.log", ".stepup/warning.log"]
            if Path(path_log).exists()
        ]
        if len(paths_log) > 0:
            self.report("WARNING", "Check logs: {}".format(" ".join(paths_log)), [])

    @allow_rpc
    def shutdown(self):
        if self.progress_bar is not None:
            self.progress_bar.stop()
        self.stop_event.set()
