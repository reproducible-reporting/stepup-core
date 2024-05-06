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
"""Asyncio utilities used in StepUp."""

import asyncio
import os
import sys

__all__ = (
    "wait_for_events",
    "stoppable_iterator",
    "stdio",
    "pipe",
)


#
# Wait for first event to be set
#


async def wait_for_events(*events: asyncio.Event, return_when):
    """Wait for the events to be set.

    Parameters
    ----------
    events
        Events to be awaited.
    return_when
        See `return_when` argument of `asyncio.wait`.
    """
    tasks = [asyncio.create_task(event.wait()) for event in events]
    done, pending = await asyncio.wait(tasks, return_when=return_when)
    for task in done:
        await task
    for task in pending:
        task.cancel()


#
# Stoppable async loop
#


async def stoppable_iterator(get_next, stop_event: asyncio.Event, args=()):
    """Iterate over messages received by calling awaitable get_next until stop_event is set.

    Parameters
    ----------
    get_next
        An awaitable that returns the next iteration.
    stop_event
        When set, the loop is interrupted.
    args
        A list of arguments to pass into get_next.
    """
    stop_task = asyncio.create_task(stop_event.wait(), name="stop_task")
    while True:
        future = asyncio.ensure_future(get_next(*args))
        done, pending = await asyncio.wait([future, stop_task], return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done and future in pending:
            await stop_task
            stop_task.result()
            future.cancel()
            break
        yield await future


#
# Setting up reader and writer pairs, other than those provided by asyncio.
#


async def stdio(
    limit=asyncio.streams._DEFAULT_LIMIT, loop=None
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Create a reader and writer connected to stdin and stdout.

    Adapted from:
    https://stackoverflow.com/questions/52089869/
    how-to-create-asyncio-stream-reader-writer-for-stdin-stdout

    Parameters
    ----------
    limit
        The maximum buffers size.
    loop
        The event loop. When not given `asyncio.get_event_loop()` is
        called, which is usually just fine.

    Returns
    -------
    reader
        The StreamReader connected to standard input.
    writer
        The StreamWriter connected to standard output.
    """
    if loop is None:
        loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader(limit=limit, loop=loop)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader, loop=loop), sys.stdin)
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop), os.fdopen(sys.stdout.fileno(), "wb")
    )
    writer = asyncio.streams.StreamWriter(writer_transport, writer_protocol, None, loop)
    return reader, writer


async def pipe(
    limit=asyncio.streams._DEFAULT_LIMIT, loop=None
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Create a connected reader and writer through `os.pipe`.

    This is mainly useful for testing, to setup an RPC client and server within one test function.
    Testing the RPC code involves two of these pipes, to set up bidirectional communication.

    Parameters
    ----------
    limit
        The maximum buffers size.
    loop
        The event loop. When not given `asyncio.get_event_loop()` is
        called, which is usually just fine.

    Returns
    -------
    reader
        The StreamReader taking data out of the pipe.
    writer
        The StreamWriter putting data into the pipe.
    """
    if loop is None:
        loop = asyncio.get_event_loop()
    fd_in, fd_out = os.pipe()
    pipe_in = open(fd_in)  # noqa: SIM115
    pipe_out = open(fd_out)  # noqa: SIM115
    reader = asyncio.StreamReader(limit=limit, loop=loop)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader, loop=loop), pipe_in)
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop), pipe_out
    )
    writer = asyncio.streams.StreamWriter(writer_transport, writer_protocol, None, loop)
    return reader, writer
