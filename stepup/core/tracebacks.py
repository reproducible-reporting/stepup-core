# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Print a step's traceback without StepUp's own frames.

When a step fails, the frames that matter are the user's.
The frames that launched the step (`_forkserver_entry`, `runpy`, ...) never do,
and StepUp's own frames only matter when the failure is a StepUp bug.
This module removes the former always and the latter only for a `UsageError`,
which is by definition a mistake in the user's code rather than in StepUp.

There are two entry points, because there are two ways a step's exception gets printed:
`print_step_traceback` for the forkserver path, which catches the exception itself,
and `install_excepthook` for steps that run as a subprocess and let the exception escape.

`STEPUP_DEBUG` disables the whole module:
every exception is then formatted by stock Python, with all frames intact.
"""

import sys
import traceback
import types
from typing import TextIO

from .exceptions import UsageError
from .utils import is_debug

__all__ = ("install_excepthook", "print_step_traceback")


STOCK_HEADER = "Traceback (most recent call last):\n"
"""The header line that stock Python puts above the frames of a traceback."""

SHORT_HEADER = "Shortened traceback (most recent call last). Set STEPUP_DEBUG=1 for details:\n"
"""The header line replacing `STOCK_HEADER` when internal frames were removed.

Announcing the shortening in the header, rather than in a footer below the exception line,
keeps the message out of the place where the eye looks for the error itself.
It is kept under 80 characters so that it does not wrap in a standard terminal.
"""

HINT = "(Traceback shortened. Set STEPUP_DEBUG=1 for the director traceback and full detail.)"
"""Footer used when there is no header line to replace, so the shortening is never silent."""

LAUNCHER_MODULES = frozenset(["runpy", "importlib._bootstrap", "importlib._bootstrap_external"])
"""Modules whose frames only show how the step was started, never why it failed."""


def print_step_traceback(exc: BaseException, file: TextIO) -> None:
    """Print the traceback of a failed step, with StepUp's own frames removed.

    Parameters
    ----------
    exc
        The exception raised by the step.
    file
        The stream to write the traceback to, usually the step's stderr.
    """
    text = _shorten(exc)
    if text is None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=file)
    else:
        file.write(text)


def install_excepthook() -> None:
    """Install a `sys.excepthook` that shortens tracebacks of uncaught exceptions.

    This is called by `stepup.core.api` at import time,
    but only when the module is imported by a step running under a director.
    Anything the hook does not shorten is passed on to the previously installed hook,
    so a `python plan.py` run outside StepUp keeps the behavior it would otherwise have.
    Calling this more than once is a no-op.
    """
    previous = sys.excepthook
    if getattr(previous, "_stepup_shortens_traceback", False):
        return

    def excepthook(
        exc_type: type[BaseException], exc: BaseException, tb: types.TracebackType | None
    ) -> None:
        text = _shorten(exc)
        if text is None:
            previous(exc_type, exc, tb)
        else:
            # `sys.stderr` is looked up here and not captured above,
            # because a step's stderr is redirected after this hook is installed.
            sys.stderr.write(text)

    excepthook._stepup_shortens_traceback = True
    sys.excepthook = excepthook


def _shorten(exc: BaseException) -> str | None:
    """Format an exception with the uninteresting frames removed.

    Only the traceback of *exc* itself is filtered.
    A chained exception (`__cause__` or `__context__`) keeps all its frames,
    as its stack was not built by StepUp's step-launching machinery.

    Parameters
    ----------
    exc
        The exception to format.

    Returns
    -------
    text
        The formatted traceback, ending in a newline,
        or `None` when there is nothing to shorten and the caller should fall back
        to stock formatting.
    """
    if is_debug():
        return None
    keep, dropped_internal = _keep_frames(exc)
    if all(keep):
        return None
    tbe = traceback.TracebackException(type(exc), exc, exc.__traceback__, compact=True)
    if len(tbe.stack) != len(keep):
        # The two are 1:1 in practice. They can only diverge through `sys.tracebacklimit`,
        # in which case the flags computed from the traceback chain no longer line up
        # and stock formatting is the only safe option.
        return None
    # The original `FrameSummary` objects are reused, which is what keeps the `~~~^^^`
    # anchors of the surviving frames intact.
    tbe.stack = traceback.StackSummary.from_list(
        [frame for frame, flag in zip(tbe.stack, keep, strict=True) if flag]
    )
    parts = list(tbe.format())
    if dropped_internal:
        parts = _mark_shortened(parts, tbe)
    return "".join(parts)


def _mark_shortened(parts: list[str], tbe: traceback.TracebackException) -> list[str]:
    """Tell the reader that frames are missing, by amending the header line if there is one.

    Parameters
    ----------
    parts
        The parts of the formatted traceback, as returned by `TracebackException.format`.
    tbe
        The exception the parts were formatted from, with its stack already filtered.

    Returns
    -------
    parts
        The parts to print, with the header replaced or a footer appended.
    """
    # `format` ends with the frames of `tbe.stack` followed by the exception line(s),
    # and puts the header of the outermost traceback right before those frames.
    # The position is computed instead of searched for, because a chained exception
    # contributes a header of its own and an exception message may contain the header text.
    head_i = len(parts) - len(tbe.stack.format()) - len(list(tbe.format_exception_only())) - 1
    if head_i >= 0 and parts[head_i] == STOCK_HEADER:
        parts[head_i] = SHORT_HEADER
        return parts
    # There is no header to replace: either every frame was dropped,
    # or this is an exception group, which `format` renders as an indented tree.
    return [*parts, HINT + "\n"]


def _keep_frames(exc: BaseException) -> tuple[list[bool], bool]:
    """Decide which frames of an exception's traceback are worth printing.

    Parameters
    ----------
    exc
        The exception whose traceback is walked, outermost frame first.

    Returns
    -------
    keep
        One flag per frame in the traceback, `True` for the frames to print.
    dropped_internal
        Whether a StepUp frame was dropped,
        i.e. whether the user is being shown less than the interpreter would show.
        Dropped launcher frames do not count:
        they carry nothing a user could want back.
    """
    usage = isinstance(exc, UsageError)
    keep = []
    dropped_internal = False
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if _is_launcher_frame(frame, outermost=len(keep) == 0):
            keep.append(False)
        elif usage and _is_stepup_frame(frame):
            # Filtering, not truncating at the first internal frame:
            # with `call()` or `script()`, the user's own function sits below
            # `stepup.core.call` in the stack and its frames must survive.
            keep.append(False)
            dropped_internal = True
        else:
            keep.append(True)
        tb = tb.tb_next
    return keep, dropped_internal


def _is_launcher_frame(frame: types.FrameType, outermost: bool) -> bool:
    """Test whether a frame belongs to the machinery that starts a step.

    These frames are dropped for every exception type, also for a plain bug in the user's
    code: they say only that StepUp ran the script, which the user's own frames already imply.

    Parameters
    ----------
    frame
        The frame to classify.
    outermost
        Whether this is the outermost frame of the traceback.
    """
    module_name = frame.f_globals.get("__name__")
    if module_name in LAUNCHER_MODULES:
        return True
    if module_name == "stepup.core.run" and frame.f_code.co_name == "_forkserver_entry":
        return True
    # `PYCODE_WRAPPER` (`run.py`) is fed to `python -` through stdin,
    # so its module-level frame is reported as `<stdin>`.
    # Only the outermost frame can be that wrapper, and requiring it is what keeps this rule
    # from also dropping a step's own frames when the step compiles or pipes in code that
    # carries the same file name.
    return outermost and frame.f_code.co_filename == "<stdin>"


def _is_stepup_frame(frame: types.FrameType) -> bool:
    """Test whether a frame belongs to StepUp or one of its extensions.

    The module name is used instead of the file name, because it is exact
    and covers extension packages such as `stepup.reprep` without further ado.
    """
    module_name = frame.f_globals.get("__name__")
    return module_name is not None and module_name.partition(".")[0] == "stepup"
