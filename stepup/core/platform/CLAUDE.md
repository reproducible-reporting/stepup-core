<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Platform Support

This file documents `stepup/core/platform/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`,
which also states how code elsewhere tests for an optional feature.

## Windows: The Proactor Event Loop (`asyncio.py`)

Windows runs `asyncio` on the Proactor event loop, which mishandles two failures
that are harmless to a server, both caused by a peer that resets its connection:

- The shutdown of a socket in `_ProactorBasePipeTransport._call_connection_lost` fails,
  and the loop leaves a half closed transport behind.
  This traceback shows up at the end of a build, and under `STEPUP_DEBUG`
  `scan_director_log` makes it fatal, which would make the tests flaky.
- An accept fails with `ERROR_NETNAME_DELETED`,
  after which `BaseProactorEventLoop._start_serving` closes the listening socket (CPython gh-93821),
  so that every later client of the director is refused.
  Upstream fixed this in Python 3.14 and 3.15 only.

`tolerate_proactor_connection_errors()` is called by `async_main` (`director/__main__.py`)
and `_async_build` (`cli/build.py`), before they start any server or connection.
It sets an exception handler on the running loop that finishes the closing of the transport
(mimicking what the skipped statements would have done),
and keeps the failure of an aborted accept (`is_aborted_accept`) out of the log.
The accept itself is handled by `_TCPAcceptor` in `rpc/server.py`.

Both depend on private details of `asyncio`,
which the code names in comments together with the Python version where they were observed.
`tests/platform/test_asyncio.py` runs them against the real transport class around a fake socket,
so a Python release that changes these details makes a test fail instead of a build.
Check them when adding a Python version to the CI matrix.
The handler recognizes only these two cases,
and passes everything else to the handler that was set before it.

## Encoding of the Standard Streams (`stdio.py`)

`rich` takes redirected output on Windows for a legacy console and then makes its lines
one column narrower, so that the layout of the reporter would depend on the platform.
A `rich` console that prints reporter-style output to standard output
(`cli/reporter.py`, `cli/clean.py` and `call.py`) therefore passes `rich_legacy_windows(stream)`
as its `legacy_windows` argument.

`write_utf8_unless_terminal()` is called first thing by `stepup` and by the director,
and by pytest through the `pytest_configure` hook of `stepup.core.pytest`,
whose report would otherwise escape the captured output of a failed example onto a single line.
A standard output or error that is not a terminal is then written in UTF-8 with `\n` line endings,
instead of in the locale encoding, which is the ANSI code page on Windows
with Python 3.14 and older and cannot represent what the reporter prints.
The environment is not used for this (`PYTHONIOENCODING`, `PYTHONUTF8`),
because the steps inherit it from the director.

## Commands on Every Platform (`popen.py`)

`popen_args()` turns a command into the `args` of `subprocess.Popen`.
`launch/subprocess.py` and `extapi.run_subprocess()` both use it,
and never the `shell=True` of `subprocess`,
which is `cmd.exe` on Windows.

- A shell command runs as `[shell, "-c", command]` with the POSIX shell of `find_shell()`:
  the one that `STEPUP_SHELL` names, `/bin/sh`, or on Windows the shell that comes with Git.
  The search skips a `sh` in the Windows directory, because that one starts WSL.
  The search is lazy, so that a build without shell steps needs no shell,
  and a failed search fails the step that needed it, not the director.
  The error message links to `SHELL_DOCS_URL`,
  which points to an anchor of `docs/installation.md`: keep both in step when that section moves.
- A command is text, and StepUp never rewrites it.
  `api.py` passes a string through unchanged (`_command_text`)
  and converts only a path object to StepUp's spelling,
  because a backslash in a string belongs to the command (an escape sequence, a regular expression).
  A path that is embedded in a command must therefore pass through `shq()`,
  which writes `/` on every platform.
  `shlex.split` would otherwise take a native Windows separator for an escape.
- On POSIX, nothing else is translated,
  because the kernel finds programs and reads shebang lines.
- On Windows, `popen.py` does that work.
  A program without a directory is looked up on the `PATH` and `PATHEXT` of the step,
  not on those of the director, and not in the current directory,
  which `CreateProcess` and `shutil.which` both try first.
  A program with a directory is resolved in the working directory of the step.
  A script runs with the interpreter of its shebang line:
  `sh` and `bash` are those of `find_shell()`, `python` and `python3` are `sys.executable`,
  and anything else is looked up on `PATH`.

Windows hands a child one command line, which every program splits by rules of its own.
`subprocess` quotes a list of arguments for programs built with the C runtime of Microsoft.
For two other families, `popen.py` builds the command line itself:
programs on the Cygwin or MSYS2 runtime, such as the shell and the tools of Git for Windows,
and batch files, which `cmd.exe` runs.
Passing a list of arguments to `subprocess` for such a program changes the arguments,
e.g. a `*` is expanded and a `&` starts another command.
