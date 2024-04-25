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
"""Watch for file changes and update the workflow accordingly."""

import asyncio

import attrs
from watchfiles import awatch, Change
from path import Path

from .workflow import Workflow
from .reporter import ReporterClient
from .utils import myrelpath


__all__ = ("Watcher",)


@attrs.define
class Watcher:
    workflow: Workflow = attrs.field()
    reporter: ReporterClient = attrs.field()
    interrupt: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    # Two attributes used to wait for a specific event
    deleted: set[Path] = attrs.field(init=False, factory=set)
    added: set[Path] = attrs.field(init=False, factory=set)
    # For waiting for a specific change
    changed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    async def loop(self):
        await self.reporter("PHASE", "watch")
        directories = [
            directory for directory in self.workflow.used_directories if Path(directory).is_dir()
        ]
        self.changed.clear()
        async for changes in awatch(*directories, recursive=False, stop_event=self.interrupt):
            for change, path in sorted(changes):
                path = myrelpath(path)
                if self.workflow.is_relevant(path):
                    if change in (Change.modified, Change.deleted):
                        await self.reporter("DELETED", path)
                        self.deleted.add(path)
                        self.added.discard(path)
                        self.changed.set()
                    if change in (Change.modified, Change.added):
                        await self.reporter("ADDED", path)
                        self.deleted.discard(path)
                        self.added.add(path)
                        self.changed.set()
        # Wait until the interrupt is set, so we can safely clear it for the next round.
        await self.interrupt.wait()
        self.interrupt.clear()
        self.changed.clear()
        # Feed all updates to the worker and clean up some more.
        self.workflow.process_watcher_changes(self.deleted, self.added)
        self.deleted.clear()
        self.added.clear()
