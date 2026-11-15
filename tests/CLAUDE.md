<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Test Structure

How the test suite is laid out.
The commands for *running* tests, and the constraints on how to invoke `pytest`,
are in the top-level `CLAUDE.md`.

## Layout

The test modules mirror the layout of `stepup/core/`:
the tests of a subpackage are in the directory of the same name
(`tests/graph/`, `tests/cli/`, ...),
and those of the top-level modules are in `tests/` itself, together with `test_examples.py`.
A test follows the code it tests, also when that code moves to another module.

The directories have no `__init__.py`,
so the file name of every test module must be unique in the whole suite.
Fixtures are defined in `tests/conftest.py`.
Helpers that tests import, such as `fake_hash` or `ConsoleHost`, are in `tests/core_common.py`,
which every test module can import because `pythonpath = ["tests"]` in `pyproject.toml`.
Import such helpers from `core_common`, never from `conftest`.

## Timeouts

Every test is bounded by `pytest-timeout`, configured in `pyproject.toml`.
Setup, call and teardown of one test share a budget of 15 seconds,
which is very generous for the fast selection (`-k "not test_example"`).
Without it, a test can hang forever instead of failing,
because the asynchronous RPC client takes no timeout
and the director waits for its steps without a deadline.
`tests/cli/test_interrupt.py` and `tests/test_examples.py` raise the budget with a
module-level `timeout` marker, because their tests police their own deadlines.
Keep those markers above the deadline the module enforces itself,
so that a failure is reported by the assertion that knows what went wrong.

The budget is not a complete safety net.
`pytest-timeout` cancels the timer of a test as soon as pytest reports a failure,
which leaves the teardown of a test whose setup failed entirely unguarded.
A fixture that waits for a subprocess must therefore bound that wait itself,
as the `client` fixture does with `SHUTDOWN_TIMEOUT`.

## Coding Conventions

`tests/test_conventions.py` is a two-line subclass of `ConventionTests`,
which lives in `stepup/core/pytest.py` together with the other pytest helpers.
The checks are kept there, not in `tests/`,
so that extension packages can impose the same conventions with the same two lines.
Add a new convention check to that class,
unless it can only ever apply to StepUp Core.

## Terminal Tests

`tests/cli/test_interrupt.py` and `tests/cli/test_console.py`
run `stepup build` as a real process on a terminal of its own,
to test Ctrl-C, Ctrl-Z and the keyboard commands end to end.
The first uses a pseudo terminal and only runs on POSIX (`requires_termios`).
The second uses a new console and only runs on Windows (`requires_windows_console`),
with `ConsoleHost` in `tests/core_common.py` playing the user of that console.
A scenario about terminal signals or keys that makes sense on both platforms belongs in both.

## Platforms and Capability Markers

The suite runs on Linux, macOS and Windows.
A test that needs something that not every platform offers carries a marker,
instead of a `skipif` on `sys.platform`.
`CAPABILITY_MARKERS` in `tests/conftest.py` maps each marker to the predicate that tests for it
(mostly those of `stepup/core/platform/oscompat.py`) and to the reason shown for a skipped test.
Register a new marker under `markers` in `pyproject.toml` as well.

Write a test so that it runs everywhere before reaching for a marker.
Behavior that is only observable on one platform, such as the spelling of Windows paths,
is tested on every platform by calling the platform-specific code directly,
or by patching `sys.platform` or an `oscompat` predicate.
Such a test is necessary but not sufficient, as `stepup/core/CLAUDE.md` explains for paths:
the final check is a run on the real platform.

- `tools/winvm/winvm.sh test ARGS` runs the suite on Windows, see `tools/winvm/README.md`.
  The VM copies the Linux working tree, so it never sees what Git does to the files on a runner.
  The CRLF checkout of GitHub's Windows runners is the example:
  `.gitattributes` makes every platform check out LF.
- `tools/macci/macci.sh test ARGS` runs it on a GitHub-hosted macOS runner.
- `.github/workflows/pytest.yaml` runs a unit job and an example job per platform,
  with a matrix of the oldest and newest Python,
  of the watcher backends and of the RPC transports.
  A setting that selects another code path (`STEPUP_WATCHER`, `STEPUP_WATCHER_RECURSIVE`,
  `STEPUP_RPC_TRANSPORT`, `STEPUP_BUILD_FORKSERVER`) is tested through an entry of that matrix,
  not by a test that sets it, so that the whole suite covers it.
- Windows has no forkserver and one watcher backend, so the examples run once there.
- A complete run of the examples takes about 10 minutes on Windows,
  against about 5 minutes on Linux.

## Integration Examples

`tests/examples/*/` contains integration test cases,
each with `plan.py`, `main.sh`, and `expected_stdout*.txt` / `expected_graph*.txt`.
These are run by `tests/test_examples.py`.

The conventions for **writing** an example live in `tests/examples/README.md`:
the `main.sh` boilerplate, the `& #` redirect trick, the executable-bit requirement,
and how `expected_*` files are regenerated
(which needs empty placeholder files to exist first, or nothing is written).
They are loaded automatically via `tests/examples/CLAUDE.md` when editing an example.
Keep that README the single source of truth: do not restate its rules here.

What belongs here instead, because it concerns `tests/test_examples.py` itself:

- An example that needs something that not every platform or configuration offers
  is added to the set named after that capability.
  `EXAMPLE_MARKERS` maps each set to a marker of `CAPABILITY_MARKERS` in `tests/conftest.py`,
  which skips the example where the capability is missing:
    - `EXAMPLES_REQUIRES_FORKSERVER`: skipped without a forkserver,
      which includes `STEPUP_BUILD_FORKSERVER=0`.
      CI runs the suite twice on Linux and macOS, with `STEPUP_BUILD_FORKSERVER=1` and `=0`,
      and once on Windows, which has no forkserver.
    - `EXAMPLES_REQUIRE_FILE_MODES`: the example changes permission bits with `chmod`,
      or needs a file without executable bit.
    - `EXAMPLES_REQUIRE_SYMLINKS`: `main.sh` creates a symbolic link,
      which takes a privilege on Windows.
    - `EXAMPLES_REQUIRE_USR_BIN_ENV`: the example uses the file `/usr/bin/env` as such.

  Every entry has a comment with its reason.
  Keep these sets short: an example that fails on one platform is ported when possible,
  following the rules for portable scripts in `tests/examples/README.md`,
  and only skipped when the feature it tests does not exist there.

`stepup/core/pytest.py` holds the pytest helpers that drive these workflows,
including the comparison of `current_*` against `expected_*` files.
`run_example` runs `main.sh` through `_run_main`, which has an implementation per platform,
both of which stop whatever the example leaves behind:
on POSIX, `main.sh` runs in a session of its own that is signaled afterwards,
and on Windows, with the bash of Git for Windows in a hidden console of its own,
inside a Job Object that is terminated afterwards.
It also normalizes what differs between platforms before the comparison,
so that one `expected_*` file serves all of them:
the separators in the `File "..."` lines of a traceback,
and, where files have no permission bits, the file modes and the digests of step hashes
(`mask_file_modes`, which leaves the digests of files alone).
On Windows, it puts a shell script `python3` on the `PATH` of `main.sh`
(`_write_python3_shim`), because Windows has no such program.
Users need the same wrapper for shell steps that start a script with a `python3` shebang,
which `docs/platforms.md` describes.
A failing comparison prints a unified diff, together with `main.log` and the logs under `.stepup/`.
The functions that normalize the output before that comparison
are unit-tested in `tests/test_pytest.py`, not in `tests/test_examples.py`:
the fast selection `-k "not test_example"` also deselects every test in a module of that name.
