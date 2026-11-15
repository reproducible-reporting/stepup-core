<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# The `stepup` Command Line

This file documents `stepup/core/cli/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Command Line Interface (`stepup/core/__main__.py`, `tool.py`)

Each `stepup` subcommand is a **tool**, registered through a `stepup.tools` entry point
so that extension packages can add their own.
`tool.py` holds what the tools have in common (the `ToolFunc` signature,
`print_error`, and the read-only access to `GRAPH_DB`),
`stepup/core/__main__.py` parses the command line and dispatches to the tool.

**The exit status is decided by the exception that reaches `main()`,
and `__main__.py` is the only module that prints an error and picks an exit code.**

- A `ToolFunc` returns `None`. A return code is not a way to signal an error.
- A mistake the user can fix is raised as a `UsageError`, in a tool usually a `ToolError`.
  `main()` prints it as a short `ERROR:` message and exits with `ReturnCode.INTERNAL`.
- Any other exception keeps its traceback, because it is a bug in StepUp.
  `STEPUP_DEBUG` turns the previous case into this one.
- A tool that needs a return code of its own calls `sys.exit`,
  which `main()` lets through untouched.
  Only `build` does so, to report the `ReturnCode` bit flags of a build.
  `config` is the one tool that also renders its own errors,
  since showing a problem on the line of the setting it concerns is what the tool is for.

The point of concentrating this in `main()` is that a tool needs no error handling of its own.
A helper such as `interact.py`'s `_connect_director` restates one exception as
another, but never prints, exits, or decides whether a traceback is warranted.

## Windows: Console Control Events and Keys (`build.py`, `terminal.py`)

The TUI and the director handle the signals of `oscompat.abort_signals()`
with `signal_handlers()` (`platform/asyncio.py`),
which also serves the Proactor event loop, where `loop.add_signal_handler` does not exist.
On Windows, these signals are `SIGINT` and `SIGBREAK`,
which is how Python reports the console control events of Ctrl-C and Ctrl-Break.
`SIGTERM` is defined but never delivered, because a Windows process is terminated without notice.

- A console control event cannot be sent to one process.
  The console delivers it to all its processes, or to those of one process group.
  The TUI therefore starts the director without creation flags,
  in the same console and the same process group,
  and never forwards an event (`_terminal_broadcasts`).
  A director in a console or group of its own would not notice a Ctrl-C.
- Of the two, only a Ctrl-C is kept away from the steps by their process groups.
  A Ctrl-Break reaches them directly, at the same time as the director.
- A process that was started in a new process group ignores Ctrl-C,
  and so does everything it starts, because this setting is inherited.
  StepUp leaves the setting alone, so `stepup build` then only reacts to Ctrl-Break,
  much like a background job on POSIX.
  The tests undo it with `SetConsoleCtrlHandler` (`tests/console_host.py`),
  because a test runner may be in that state.
- Keys are read with `platform.winapi.read_console_keys`, in a thread,
  and never with `msvcrt.getwch`.
  The latter switches off the input processing of the console for as long as it waits.
  With a thread waiting in `getwch`, a Ctrl-C arrived as the character `\x03`
  and no longer as an event, when this was tested on Windows 11 24H2.
- A console is recognized with `platform.stdio.is_terminal`, not with `isatty()`,
  which is also true for `NUL` on Windows.
- A console that goes away does not hang up its processes as a POSIX terminal does.
  When the SSH session of a build was closed, on Windows 11 24H2 with OpenSSH,
  the TUI ended at once, without a signal, and the director carried on in its watch phase.
  The TUI therefore puts the director in a Job Object of its own (`_confine_director`),
  as the director does with its steps, so that nothing of a build outlives `stepup build`.
- A killed process has no signal to show for it.
  `Process.kill()` gives the director exit code 1, a valid `ReturnCode`,
  so `TerminalSignalHandler` remembers that it killed the director.
  Any exit code that is not a `ReturnCode` combination, e.g. `0xC000013A`, becomes `INTERNAL`.

`tests/cli/test_console.py` covers this end to end,
as `tests/cli/test_interrupt.py` does for POSIX.
It runs `stepup build` in a new console and generates the events and keys in it,
so it needs no interactive session.
