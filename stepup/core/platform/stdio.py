# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Recognize terminals and set the encoding of the standard streams."""

import io
import logging
import sys
from typing import TextIO

if sys.platform == "win32":
    from .winapi import is_console

__all__ = (
    "is_terminal",
    "rich_legacy_windows",
    "write_utf8_unless_terminal",
)

logger = logging.getLogger(__name__)


def is_terminal(stream: TextIO | None) -> bool:
    """Check whether a standard stream of this process is connected to a terminal.

    On Windows, this is stricter than `isatty()`, which is also true for other character devices,
    e.g. for a stream that is redirected from or to `NUL`.
    """
    if stream is None:
        return False
    if sys.platform == "win32":
        try:
            fd = stream.fileno()
        except OSError:
            # The stream is not backed by a file descriptor.
            return False
        return is_console(fd)
    return stream.isatty()


def rich_legacy_windows(stream: TextIO | None) -> bool | None:
    """Decide the `legacy_windows` argument of a `rich` console that writes to a stream.

    Left to itself, `rich` takes every stream that is not a modern Windows console
    for a legacy one, a file or a pipe included,
    and then makes its lines one character shorter than the width it was given.
    Observed with rich 15.0 on Windows 11 24H2.

    Returns
    -------
    legacy_windows
        `False` for a stream that is not a terminal,
        so that redirected output has the same layout on every platform,
        and `None` for a terminal, which leaves the detection to `rich`.
    """
    return None if is_terminal(stream) else False


def write_utf8_unless_terminal() -> None:
    """Make the standard output and error write UTF-8 and `\\n`, unless they are a terminal.

    Output that does not go to a terminal is otherwise written in the locale encoding,
    e.g. the ANSI code page on Windows with Python 3.14 and older,
    which cannot represent everything that StepUp prints,
    and with the line endings of the platform.
    A terminal is left alone, because Python already writes to it in the encoding it expects.

    An environment variable such as `PYTHONIOENCODING` is no alternative,
    because every child process would inherit it.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not is_terminal(stream):
            # Without an explicit `errors`, `reconfigure` would reset that of `stderr` to `strict`.
            stream.reconfigure(encoding="utf-8", errors=stream.errors, newline="\n")
