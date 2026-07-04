# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities used by multiple test modules."""

import asyncio

import attrs

from stepup.core.rpc import allow_rpc


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
    def shutdown(self):
        self.stop_event.set()

    def not_allowed(self):
        print("This method should not be callable.")
