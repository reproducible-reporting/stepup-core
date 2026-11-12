# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""End-to-end tests for aborting and steering a build from a Windows console.

These are the Windows counterpart of `test_interrupt.py`.
They run `stepup build` as a real child process in a console of its own,
because the behavior under test is about console control events, process groups and exit codes,
none of which survive being mocked out.
`ConsoleHost` plays the user of that console:
it generates a Ctrl-C or Ctrl-Break for all processes in the console, as a key press does,
and types keys by writing them to the input buffer of the console.
"""

import re
import sys
import time

import pytest
from core_common import ConsoleHost

from stepup.core.enums import ReturnCode
from stepup.core.path import Path
from stepup.core.utils import is_process_running

PLAN = """#!/usr/bin/env python3
from stepup.core.api import static, step

static("leaf.py")
step({command!r}, inp=["leaf.py"], out=["never.txt"], shell={shell!r})
"""

LEAF = """#!/usr/bin/env python3
import os
import signal
import sys
import time

if "stubborn" in sys.argv[1:]:
    signal.signal(signal.SIGBREAK, signal.SIG_IGN)
with open("leaf.pid.tmp", "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
os.replace("leaf.pid.tmp", "leaf.pid")
time.sleep(300)
"""
"""The long-running process each scenario waits for and expects to be stopped.

It announces its pid in a file, which only appears when it is complete.
"""

TIMEOUT = 60.0
"""Hard cap for a single scenario, well above all shutdown grace periods."""

pytestmark = [pytest.mark.timeout(3 * TIMEOUT), pytest.mark.requires_windows_console]
"""The console that every scenario runs in, and the budget for a whole scenario.

A scenario polices its own deadlines with `TIMEOUT`.
Setup, call and teardown share the budget,
and it must stay above their combined worst case,
so that a scenario that runs out of time fails on its own assertion,
which shows what StepUp printed,
rather than on the much less informative timeout of the test suite.
"""


def wait_for_leaf(host: ConsoleHost, path_tmp: Path) -> int:
    """Wait until the long-running process of the step runs, and return its pid."""
    path_pid = path_tmp / "leaf.pid"
    deadline = time.monotonic() + TIMEOUT / 2
    while time.monotonic() < deadline:
        if path_pid.is_file():
            return int(path_pid.read_text(encoding="utf-8"))
        if host.process.poll() is not None:
            break
        time.sleep(0.1)
    raise AssertionError(f"The step did not start in time. Output of StepUp:\n{host.output()}")


def wait_for_output(host: ConsoleHost, text: str) -> None:
    """Wait until StepUp has printed the given text."""
    deadline = time.monotonic() + TIMEOUT / 2
    while time.monotonic() < deadline:
        if text in host.output():
            return
        time.sleep(0.05)
    raise AssertionError(f"StepUp did not print {text!r}. Its output:\n{host.output()}")


def wait_gone(pid: int, timeout: float) -> bool:
    """Wait until a process is gone, returning False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and is_process_running(pid):
        time.sleep(0.1)
    return not is_process_running(pid)


@pytest.fixture
def stepup_on_console(request: pytest.FixtureRequest, path_tmp: Path):
    """Run `stepup build` with one long-running step in a new console, and clean up afterwards.

    The `(command, shell)` pair of the step can be overridden with indirect parametrization,
    as long as the command still runs `leaf.py`.

    Yields
    ------
    host, leaf_pid
        The console in which StepUp runs, and the pid of the long-running process of the step.
    """
    command, shell = getattr(request, "param", ("./leaf.py", False))
    (path_tmp / "plan.py").write_text(PLAN.format(command=command, shell=shell), encoding="utf-8")
    (path_tmp / "leaf.py").write_text(LEAF, encoding="utf-8")
    host = ConsoleHost.start(
        [sys.executable, "-m", "stepup.core", "build", "--no-progress"],
        path_tmp,
        path_tmp / "stepup.out",
    )
    try:
        yield host, wait_for_leaf(host, path_tmp)
    finally:
        host.close()


def test_ctrl_c_aborts_the_build(stepup_on_console) -> None:
    """A single Ctrl-C stops the running step, and StepUp exits on its own."""
    host, leaf_pid = stepup_on_console
    host.act("ctrl-c")
    returncode = host.wait(TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value, (
        f"An aborted build must set the INTERRUPTED bit, got returncode {returncode}."
    )
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The running step outlived the build."
    output = host.output()
    assert "Interrupted by SIGINT. Waiting for the director to stop." in output
    # The console delivers the event to the director as well, so nobody forwards it.
    assert "Aborting the build (SIGINT)." in output
    assert "Interrupting running steps (CTRL_BREAK_EVENT)." in output


def test_ctrl_c_lets_the_director_finish_its_shutdown(stepup_on_console, path_tmp: Path) -> None:
    """The terminal user interface waits for the director instead of cutting it short."""
    host, _ = stepup_on_console
    host.act("ctrl-c")
    host.wait(TIMEOUT)
    assert "Traceback" not in (path_tmp / ".stepup" / "director.log").read_text(encoding="utf-8")
    # 'See you!' is the last thing the director reports, after writing its logs.
    assert "See you!" in (path_tmp / ".stepup" / "success.log").read_text(encoding="utf-8")


def test_ctrl_break_aborts_the_build(stepup_on_console) -> None:
    """A Ctrl-Break aborts the build as well, and it reaches the steps without the director."""
    host, leaf_pid = stepup_on_console
    host.act("ctrl-break")
    returncode = host.wait(TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The running step outlived the build."
    output = host.output()
    assert "Interrupted by SIGBREAK. Waiting for the director to stop." in output
    assert "Aborting the build (SIGBREAK)." in output


@pytest.mark.parametrize(
    "stepup_on_console",
    [(f'"{Path(sys.executable)}" leaf.py | cat', True)],
    indirect=True,
)
def test_ctrl_c_stops_work_behind_a_shell_wrapper(stepup_on_console) -> None:
    """A pipeline keeps the shell as a wrapper, so stopping that one process is not enough.

    Steps lead a process group of their own, which is what lets the director send
    the console control event to the whole group and reach the process doing the actual work.
    """
    host, leaf_pid = stepup_on_console
    host.act("ctrl-c")
    host.wait(TIMEOUT)
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The process behind the shell wrapper kept running."


@pytest.mark.parametrize("stepup_on_console", [("./leaf.py stubborn", False)], indirect=True)
def test_second_ctrl_c_kills_a_step_that_ignores_the_first(stepup_on_console) -> None:
    """A step that ignores the interrupt is killed, at once when the user insists."""
    host, leaf_pid = stepup_on_console
    host.act("ctrl-c")
    wait_for_output(host, "Interrupting running steps (CTRL_BREAK_EVENT).")
    assert is_process_running(leaf_pid)
    host.act("ctrl-c")
    returncode = host.wait(TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The stubborn step outlived the build."
    assert "Killing unresponsive steps (TerminateJobObject)." in host.output()


def test_q_twice_shuts_down_and_interrupts(stepup_on_console) -> None:
    """The first `q` waits for the running step, and the second one interrupts it."""
    host, leaf_pid = stepup_on_console
    host.act("keys q")
    wait_for_output(host, "Waiting for steps to complete before shutdown.")
    assert is_process_running(leaf_pid)
    host.act("keys q")
    returncode = host.wait(TIMEOUT)
    # A shutdown that the user asks for with a key is not an interruption by a signal.
    assert not returncode & ReturnCode.INTERRUPTED.value
    assert returncode & ReturnCode.FAILED.value
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The running step outlived the build."
    assert "Interrupting running steps (CTRL_BREAK_EVENT)." in host.output()


def test_keys_reach_the_director(stepup_on_console, path_tmp: Path) -> None:
    """Keys typed in the console are commands, and a Ctrl-C still works while they are read."""
    host, leaf_pid = stepup_on_console
    host.act("keys g")
    wait_for_output(host, "Wrote graph to graph.txt")
    assert (path_tmp / "graph.txt").is_file()
    host.act("keys x")
    wait_for_output(host, "Unsupported key x")
    host.act("ctrl-c")
    returncode = host.wait(TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The running step outlived the build."


def test_killed_stepup_leaves_nothing_behind(stepup_on_console, path_tmp: Path) -> None:
    """The director and its steps end when `stepup build` is terminated without notice.

    Nothing tells them: a terminated Windows process gets no chance to do anything.
    They end because the director is a member of a Job Object that only `stepup build` holds.
    """
    host, leaf_pid = stepup_on_console
    director_log = (path_tmp / ".stepup" / "director.log").read_text(encoding="utf-8")
    director_pid = int(re.search(r"^PID (\d+)$", director_log, re.MULTILINE).group(1))
    assert is_process_running(director_pid)
    host.act("kill")
    host.wait(TIMEOUT)
    assert wait_gone(director_pid, TIMEOUT / 2), "The director outlived stepup build."
    assert wait_gone(leaf_pid, TIMEOUT / 2), "The running step outlived stepup build."
