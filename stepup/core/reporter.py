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
"""Terminal output of StepUp's runner progress and observed file changes."""

import asyncio
import contextlib
from collections import Counter
from time import perf_counter
from typing import Self

import attrs
from rich.console import Console
from rich.markup import escape as escape_markup
from rich.progress import BarColumn, MofNCompleteColumn, TaskID, TextColumn
from rich.progress import Progress as ProgressBar
from rich.theme import Theme

from .rpc import AsyncRPCClient, BaseAsyncRPCClient, DummyAsyncRPCClient, allow_rpc
from .step import StepState

__all__ = ("ReporterClient", "ReporterHandler")


@attrs.define
class ReporterClient:
    socket_path: str | None = attrs.field(default=None)
    client: BaseAsyncRPCClient = attrs.field(factory=DummyAsyncRPCClient)

    @classmethod
    @contextlib.asynccontextmanager
    async def socket(cls, path: str | None) -> Self:
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

    async def update_step_counts(self, step_counter: Counter[StepState, int]):
        if self.client is not None:
            await self.client.call.update_step_counts(step_counter)

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


@attrs.define
class ReporterHandler:
    show_perf: bool = attrs.field()
    stop_event: asyncio.Event = attrs.field(factory=asyncio.Event)
    _num_workers: int = attrs.field(init=False, default=0)
    _step_counts: Counter[StepState, int] = attrs.field(init=False, factory=Counter)
    _num_digits: int = attrs.field(init=False, default=3)
    console: Console = attrs.field(init=False)
    progress_bar: ProgressBar = attrs.field(init=False)
    task_id_running: TaskID = attrs.field(init=False)
    task_id_step: TaskID = attrs.field(init=False)
    start: float = attrs.field(init=False, factory=perf_counter)

    @console.default
    def _default_console(self):
        theme = Theme(
            {
                "rule.line": "bold gray42",
                "bar.complete": "bold grey82",
                "bar.finished": "bold grey82",
            }
        )
        return Console(highlight=False, theme=theme)

    @progress_bar.default
    def _default_progress_bar(self):
        progress_bar = ProgressBar(
            TextColumn("{task.description}"),
            BarColumn(None),
            MofNCompleteColumn(),
            transient=True,
            console=self.console,
        )
        progress_bar.start()
        return progress_bar

    @task_id_running.default
    def _default_task_id_running(self):
        return self.progress_bar.add_task("🛠 ", total=0, visible=True)

    @task_id_step.default
    def _default_task_id_step(self):
        return self.progress_bar.add_task("✔ ", total=0, visible=True)

    @allow_rpc
    def shutdown(self):
        self.progress_bar.stop()
        self.stop_event.set()

    @allow_rpc
    def report(self, action: str, description: str, pages: list[tuple[str, str]]):
        # Progress bar
        nsuc = self._step_counts[StepState.SUCCEEDED]
        nrun = self._step_counts[StepState.RUNNING]
        npen = self._step_counts[StepState.PENDING] + self._step_counts[StepState.QUEUED]
        nd = max(self._num_digits, len(str(nsuc)), len(str(nrun)), len(str(npen)))
        self._num_digits = nd
        self.progress_bar.update(self.task_id_running, completed=nrun, total=self._num_workers)
        self.progress_bar.update(self.task_id_step, completed=nsuc, total=nsuc + nrun + npen)

        # Action info
        action_color = {
            "START": "blue",
            "FAIL": "red",
            "ERROR": "red",
            "SUCCESS": "green",
            "DELETED": "yellow",
            "UPDATED": "yellow",
            "SKIP": "cyan",
            "NOSKIP": "cyan",
            "RESCHEDULE": "yellow",
            "DROPAMEND": "yellow",
            "WARNING": "yellow",
            "PHASE": "white",
        }.get(action, "magenta")
        descr_color = {"START": "grey82"}.get(action, "grey46")

        # Print action with extra info
        line = f"[bold {action_color}]{action:>10s}[/] │ [{descr_color}]{description}[/]"
        if self.show_perf:
            now = perf_counter()
            line = f"[gray46]{perf_counter() - self.start:7.2f} {nrun:{nd}d}[/] " + line
            if action == "PHASE":
                self.start = now
        self.console.print(
            line, no_wrap=self.console.is_terminal, soft_wrap=not self.console.is_terminal
        )

        # Pages if any
        for title, page in pages:
            self.console.rule(f"[grey82]{title}[/]")
            self.console.print(f"[gray42]{escape_markup(page)}[/]", soft_wrap=True)
        if len(pages) > 0:
            self.console.rule()

    @allow_rpc
    def set_num_workers(self, num_workers: int):
        self._num_workers = num_workers

    @allow_rpc
    def update_step_counts(self, step_counts: Counter[StepState, int]):
        self._step_counts = step_counts
