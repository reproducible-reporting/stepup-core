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
- Examples that only work with the forkserver must be added to
  `EXAMPLES_REQUIRES_FORKSERVER`; they are skipped when `STEPUP_BUILD_FORKSERVER=0`.
  CI runs the suite twice, with `STEPUP_BUILD_FORKSERVER=1` and `=0`.

`stepup/core/pytest.py` holds the pytest helpers that drive these workflows,
including the comparison of `current_*` against `expected_*` files.
