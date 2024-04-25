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
        # print("ECHO", file=sys.stderr)
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
        # print("SHUTDOWN", file=sys.stderr)
        self.stop_event.set()

    def not_allowed(self):
        print("This method should not be callable.")
