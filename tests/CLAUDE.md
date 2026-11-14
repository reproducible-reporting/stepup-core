<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Test Structure

How the test suite is laid out.
The commands for *running* tests, and the constraints on how to invoke `pytest`,
are in the top-level `CLAUDE.md`.

## Coding Conventions

`tests/test_conventions.py` is a two-line subclass of `ConventionTests`,
which lives in `stepup/core/pytest.py` together with the other pytest helpers.
The checks are kept there, not in `tests/`,
so that extension packages can impose the same conventions with the same two lines.
Add a new convention check to that class,
unless it can only ever apply to StepUp Core.

## Terminal Tests

`tests/test_interrupt.py` and `tests/test_console.py` run `stepup build` as a real process
on a terminal of its own, to test Ctrl-C, Ctrl-Z and the keyboard commands end to end.
The first uses a pseudo terminal and only runs on POSIX (`requires_termios`).
The second uses a new console and only runs on Windows (`requires_windows_console`),
with `ConsoleHost` in `tests/core_common.py` playing the user of that console.
A scenario about terminal signals or keys that makes sense on both platforms belongs in both.

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

- Register each new example in the `EXAMPLES` list at the top of the module,
  from which `test_example` is parametrized.
  Add the name to the `test_plan` parametrize list as well
  if the plan should also run standalone.
  The guard tests `test_examples_list_has_all_dirs` / `test_examples_list_has_no_extra`
  fail when `EXAMPLES` is out of sync with the directories under `tests/examples/`.
- An example that needs something that not every platform or configuration offers
  is added to the set named after that capability.
  `EXAMPLE_MARKERS` maps each set to a marker of `CAPABILITY_MARKERS` in `tests/conftest.py`,
  which skips the example where the capability is missing:
    - `EXAMPLES_REQUIRES_FORKSERVER`: skipped without a forkserver,
      which includes `STEPUP_BUILD_FORKSERVER=0`.
      CI runs the suite twice, with `STEPUP_BUILD_FORKSERVER=1` and `=0`.
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
The functions that normalize the output before that comparison
are unit-tested in `tests/test_pytest.py`, not in `tests/test_examples.py`:
the fast selection `-k "not test_example"` also deselects every test in a module of that name.
