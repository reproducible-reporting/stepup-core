# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.tracebacks"""

import io
import re
import sys
from collections.abc import Callable

import pytest
from path import Path

from stepup.core.exceptions import CyclicError
from stepup.core.tracebacks import (
    HINT,
    SHORT_HEADER,
    STOCK_HEADER,
    _keep_frames,
    _shorten,
    install_excepthook,
    print_step_traceback,
)

#
# Building a traceback with frames from modules that are not really involved.
#
# The filter decides from `frame.f_globals["__name__"]`, so a frame that claims to belong to
# `stepup.core.api` is indistinguishable from a real one, without a director to produce it.
# The file names need not exist: only the `<stdin>` rule looks at them.
#

Frame = tuple[str, str, str]
"""A frame to fabricate: the module name it reports, its file name and its function name."""

FORKSERVER: Frame = ("stepup.core.run", "stepup/core/run.py", "_forkserver_entry")
LAUNCH_COMMAND: Frame = ("stepup.core.run", "stepup/core/run.py", "launch_command")
RUNPY: Frame = ("runpy", "<frozen runpy>", "_run_code")
BOOTSTRAP: Frame = ("importlib._bootstrap", "<frozen importlib._bootstrap>", "_load_unlocked")
# `PYCODE_WRAPPER` (`run.py`) is fed to `python -` through stdin, so its frame is `<stdin>`.
# Only the file name matters here: the real frame is a module-level one, which cannot be
# fabricated with a function name.
WRAPPER: Frame = ("__main__", "<stdin>", "wrapper")
PLAN: Frame = ("__main__", "./plan.py", "main")
WORK: Frame = ("__main__", "./work.py", "compute")
API: Frame = ("stepup.core.api", "stepup/core/api.py", "copy")
CALL: Frame = ("stepup.core.call", "stepup/core/call.py", "driver")
EXTENSION: Frame = ("stepup.reprep.api", "stepup/reprep/api.py", "compile_latex")


def _spoof_call(module: str, filename: str, name: str, call_next: Callable) -> Callable:
    """Make a function whose frame reports *module* and *filename*, and that calls *call_next*."""
    namespace = {"__name__": module, "_call_next": call_next}
    exec(compile(f"def {name}():\n    _call_next()\n", filename, "exec"), namespace)
    return namespace[name]


def _spoof_raise(module: str, filename: str, name: str, exc: BaseException) -> Callable:
    """Make a function whose frame reports *module* and *filename*, and that raises *exc*."""
    namespace = {"__name__": module, "_exc": exc}
    exec(compile(f"def {name}():\n    raise _exc\n", filename, "exec"), namespace)
    return namespace[name]


def _raise_through(frames: list[Frame], exc: BaseException) -> BaseException:
    """Raise *exc* from the innermost of *frames* and return it with that traceback attached.

    Parameters
    ----------
    frames
        The frames to fabricate, outermost first. The innermost one raises.
    exc
        The exception to raise.

    Returns
    -------
    exc
        The same exception, whose traceback holds exactly *frames*:
        the frame of this helper is stripped.
    """
    func = _spoof_raise(*frames[-1], exc)
    for frame in reversed(frames[:-1]):
        func = _spoof_call(*frame, func)
    with pytest.raises(type(exc)):
        func()
    return exc.with_traceback(exc.__traceback__.tb_next)


def test_raise_through_builds_the_requested_frames():
    """The helper itself must be right, since every other test reads its frames."""
    exc = _raise_through([PLAN, API], CyclicError("cycle"))
    names = []
    tb = exc.__traceback__
    while tb is not None:
        names.append((tb.tb_frame.f_globals["__name__"], tb.tb_frame.f_code.co_name))
        tb = tb.tb_next
    assert names == [("__main__", "main"), ("stepup.core.api", "copy")]


#
# Tier A: launcher frames, dropped for every exception type.
#


def test_keep_frames_drops_launcher_frames_of_an_internal_error():
    """Launcher frames say only that StepUp started the step, also when the step has a bug."""
    exc = _raise_through([FORKSERVER, RUNPY, BOOTSTRAP, PLAN], RuntimeError("bug"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [False, False, False, True]
    # Nothing the user could want back was removed, so this must not trigger a hint.
    assert not dropped_internal


def test_keep_frames_drops_the_wrapper_frame():
    """Without the forkserver, the outermost frame is `PYCODE_WRAPPER`, fed in through stdin."""
    exc = _raise_through([WRAPPER, RUNPY, PLAN], RuntimeError("bug"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [False, False, True]
    assert not dropped_internal


def test_keep_frames_keeps_a_stdin_frame_that_is_not_the_outermost():
    """Only the outermost frame can be `PYCODE_WRAPPER`.

    A step that compiles or pipes in code of its own gets the same `<stdin>` file name,
    and those frames are the step's own, so they must survive.
    """
    exc = _raise_through([WRAPPER, RUNPY, PLAN, WRAPPER], RuntimeError("bug"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [False, False, True, True]
    assert not dropped_internal


def test_keep_frames_only_treats_forkserver_entry_as_a_launcher():
    """The rule is about `_forkserver_entry`, not about `run.py` as a whole."""
    exc = _raise_through([LAUNCH_COMMAND, PLAN], RuntimeError("bug"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [True, True]
    assert not dropped_internal


#
# Tier B: StepUp frames, dropped only for a usage error.
#


def test_keep_frames_keeps_stepup_frames_of_an_internal_error():
    """For a bug in StepUp, the internal frame is the bug location and must survive."""
    exc = _raise_through([PLAN, API], RuntimeError("bug"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [True, True]
    assert not dropped_internal


def test_keep_frames_drops_stepup_frames_of_a_usage_error():
    exc = _raise_through([PLAN, API], CyclicError("cycle"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [True, False]
    assert dropped_internal


def test_keep_frames_drops_extension_frames_of_a_usage_error():
    """The top-level package decides, so extensions like `stepup.reprep` are covered too."""
    exc = _raise_through([PLAN, EXTENSION, API], CyclicError("cycle"))
    keep, _ = _keep_frames(exc)
    assert keep == [True, False, False]


def test_keep_frames_keeps_user_frames_below_a_stepup_frame():
    """With `call()` or `script()`, the user's own function sits below `stepup.core.call`.

    This is why the frames are filtered instead of truncated at the first internal frame.
    """
    exc = _raise_through([PLAN, CALL, WORK, API], CyclicError("cycle"))
    keep, dropped_internal = _keep_frames(exc)
    assert keep == [True, False, True, False]
    assert dropped_internal


#
# Rendering.
#


def test_shorten_declines_when_every_frame_is_worth_printing():
    """Without anything to remove, stock formatting is what the caller should use."""
    assert _shorten(_raise_through([PLAN, WORK], CyclicError("cycle"))) is None


def test_shorten_declines_with_stepup_debug(monkeypatch: pytest.MonkeyPatch):
    """`STEPUP_DEBUG` disables the whole module, not just one of the two tiers."""
    monkeypatch.setenv("STEPUP_DEBUG", "1")
    assert _shorten(_raise_through([FORKSERVER, PLAN, API], CyclicError("cycle"))) is None


def test_shorten_declines_for_an_exception_without_traceback():
    assert _shorten(CyclicError("never raised")) is None


def test_shorten_declines_when_tracebacklimit_shrinks_the_stack(monkeypatch: pytest.MonkeyPatch):
    """The keep flags are computed from the traceback chain, which `sys.tracebacklimit` cuts.

    They can then no longer be applied positionally to `TracebackException.stack`,
    so stock formatting is the only safe option.
    """
    exc = _raise_through([FORKSERVER, PLAN, API], CyclicError("cycle"))
    monkeypatch.setattr(sys, "tracebacklimit", 1, raising=False)
    assert _shorten(exc) is None


def test_shorten_replaces_the_header_when_stepup_frames_are_dropped():
    exc = _raise_through([FORKSERVER, RUNPY, PLAN, API], CyclicError("cycle"))
    text = _shorten(exc)
    assert text.startswith(SHORT_HEADER)
    assert STOCK_HEADER not in text
    assert 'File "./plan.py"' in text
    assert "api.py" not in text
    assert "runpy" not in text
    assert text.endswith("stepup.core.exceptions.CyclicError: cycle\n")


def test_shorten_keeps_the_header_when_only_launcher_frames_are_dropped():
    """A plain bug in the user's code gets no StepUp footer under it.

    The launcher frames say only that `runpy` ran the script,
    which the surviving `plan.py` frame already implies.
    """
    text = _shorten(_raise_through([FORKSERVER, RUNPY, PLAN], RuntimeError("bug")))
    assert text.startswith(STOCK_HEADER)
    assert SHORT_HEADER not in text
    assert HINT not in text


def test_shorten_appends_a_hint_when_no_frame_survives():
    """Without frames there is no header to amend, so the shortening is announced below."""
    text = _shorten(_raise_through([FORKSERVER, API], CyclicError("cycle")))
    assert STOCK_HEADER not in text
    assert SHORT_HEADER not in text
    assert text == f"stepup.core.exceptions.CyclicError: cycle\n{HINT}\n"


def test_shorten_leaves_a_chained_exception_alone():
    """Only the traceback of the exception itself is filtered, and only its header amended."""
    cause = _raise_through([API], ValueError("root cause"))
    exc = _raise_through([FORKSERVER, PLAN, API], CyclicError("cycle"))
    exc.__cause__ = cause
    text = _shorten(exc)
    # The cause was not raised by StepUp's step-launching machinery, so it keeps its frames.
    assert text.startswith(STOCK_HEADER)
    assert "api.py" in text.split("The above exception")[0]
    assert text.count(SHORT_HEADER) == 1
    assert text.endswith("stepup.core.exceptions.CyclicError: cycle\n")


def test_shorten_keeps_the_anchors_of_a_surviving_frame(path_tmp: Path):
    """Rebuilding the stack must not cost the `~~~^^^` anchors of the frames that survive.

    They are preserved because `StackSummary.from_list` reuses the original `FrameSummary`
    objects, instead of recomputing them from the code and the line number.
    """
    path_plan = path_tmp / "plan.py"
    with open(path_plan, "w") as fh:
        fh.write("def main():\n    _call_next() + _call_next()\n")
    namespace = {
        "__name__": "__main__",
        "_call_next": _spoof_raise(*API, CyclicError("cycle")),
    }
    with open(path_plan) as fh:
        exec(compile(fh.read(), path_plan, "exec"), namespace)
    with pytest.raises(CyclicError) as exc_info:
        namespace["main"]()
    exc = exc_info.value
    text = _shorten(exc.with_traceback(exc.__traceback__.tb_next))
    assert "_call_next() + _call_next()" in text
    assert re.search(r"^\s+[~^]+$", text, re.MULTILINE) is not None


#
# Entry points.
#


def test_print_step_traceback_writes_the_shortened_text():
    file = io.StringIO()
    print_step_traceback(_raise_through([FORKSERVER, PLAN, API], CyclicError("cycle")), file)
    text = file.getvalue()
    assert text.startswith(SHORT_HEADER)
    assert "api.py" not in text


def test_print_step_traceback_falls_back_to_stock_formatting():
    """When there is nothing to shorten, the output is what the interpreter would print."""
    file = io.StringIO()
    print_step_traceback(_raise_through([PLAN, WORK], RuntimeError("bug")), file)
    text = file.getvalue()
    assert text.startswith(STOCK_HEADER)
    assert 'File "./plan.py"' in text
    assert 'File "./work.py"' in text
    assert text.endswith("RuntimeError: bug\n")


def test_install_excepthook_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    """Importing `stepup.core.api` twice must not stack two hooks."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    install_excepthook()
    hook = sys.excepthook
    assert hook is not sys.__excepthook__
    install_excepthook()
    assert sys.excepthook is hook


def test_install_excepthook_chains_what_it_does_not_shorten(monkeypatch: pytest.MonkeyPatch):
    """A `python plan.py` outside StepUp keeps the behavior it would otherwise have."""
    calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: calls.append(args))
    install_excepthook()
    exc = _raise_through([PLAN, WORK], RuntimeError("bug"))
    sys.excepthook(type(exc), exc, exc.__traceback__)
    assert calls == [(RuntimeError, exc, exc.__traceback__)]


def test_install_excepthook_writes_to_the_current_stderr(monkeypatch: pytest.MonkeyPatch):
    """A step's stderr is redirected after the hook is installed, so it is looked up late."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    install_excepthook()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    exc = _raise_through([FORKSERVER, PLAN, API], CyclicError("cycle"))
    sys.excepthook(type(exc), exc, exc.__traceback__)
    assert stderr.getvalue().startswith(SHORT_HEADER)
