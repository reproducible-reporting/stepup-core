# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities used by multiple test modules."""

import asyncio

import attrs

from stepup.core.exceptions import CyclicError
from stepup.core.rpc import allow_rpc


async def settled_task_names() -> list[str]:
    """The names of the tasks that are still pending, after letting the event loop settle.

    A few no-op sleeps are needed because a cancelled task only completes
    when the event loop gets to deliver the cancellation.
    """
    for _ in range(5):
        await asyncio.sleep(0)
    current = asyncio.current_task()
    return sorted(task.get_name() for task in asyncio.all_tasks() if task is not current)


@attrs.define
class EchoHandler:
    """A simple handler for unit testing the RPC module in StepUp."""

    name: str = attrs.field()
    stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    @allow_rpc
    async def echo(self, msg: str, delay: float = 0.0) -> str:
        if delay > 0:
            await asyncio.sleep(delay)
        return f"{self.name}: {msg}"

    @allow_rpc
    def lcg(self, seed, modulus=71, multiplier=45, increment=91) -> int:
        """Implementation of a linear congruential generator iteration.

        See https://en.wikipedia.org/wiki/Linear_congruential_generator
        """
        return (multiplier * seed + increment) % modulus

    @allow_rpc
    def greet(self, name: str) -> str:
        """Say hello to `name`, a parameter that the client's `__call__` also has."""
        return f"{self.name}: hello {name}"

    @allow_rpc
    def raise_usage(self):
        """Fail with a mistake the caller can fix, i.e. a `UsageError` subclass."""
        raise CyclicError("cyclic")

    @allow_rpc
    def raise_internal(self):
        """Fail with something that indicates a bug in the server."""
        raise RuntimeError("bug")

    @allow_rpc
    def shutdown(self):
        self.stop_event.set()

    def not_allowed(self):
        print("This method should not be callable.")
