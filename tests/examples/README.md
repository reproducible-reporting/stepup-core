<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->
# Examples

Each subdirectory of `examples` contains a short example of how to use StepUp.
The script `main.sh` executes the whole example.
Example directories also contain a `README.txt` file describing what is being demonstrated.

All examples are included in the test suite,
for which some technicalities are required in the `main.sh` scripts.
These technical details are not relevant for understanding how StepUp works,
so you may ignore them when you just use them as a reference for how to make something with StepUp.
(Some examples are pathological edge cases, not intended as templates for real use cases.)

When writing new examples, the following conventions ensure that they are properly tested:

- **Register the example** in the `EXAMPLES` list at the top of `tests/test_examples.py`,
  from which `test_example` is parametrized.
  Examples are *not* auto-discovered from this directory,
  but forgetting to register one is not silent:
  the guard tests `test_examples_list_has_all_dirs` and `test_examples_list_has_no_extra`
  fail when `EXAMPLES` and the directories under `tests/examples/` disagree.
  If its `plan.py` should also be exercised
  standalone (without StepUp), add the name to the `test_plan` list as well.

- CI runs the whole example suite twice, once with `STEPUP_BUILD_FORKSERVER=1` and once with
  `STEPUP_BUILD_FORKSERVER=0`, so an example must pass under **both** the forkserver and the
  plain-subprocess execution paths. Do not pin `forkserver` in a per-example `stepup.toml`
  unless the example is specifically about one path.

- To facilitate debugging, the shebang line runs bash in verbose mode: `#!/usr/bin/env -S bash -x`.

- After the shebang, each `main.sh` sources the shared boilerplate with
  `source ../example.rc`.

  The test runner copies `example.rc` next to the per-example working directory,
  so `../example.rc` resolves both under the test harness and when running
  `bash main.sh` directly inside an example directory.

- A local `.gitignore` file lists all StepUp-managed outputs,
  so that they are not accidentally committed to git.
  Include at least the following:

  ```text
  .stepup/
  current_*
  # Add any other expected outputs here
  ```

- All scripts that StepUp will execute as steps — including `main.sh`, `plan.py`, and any
  worker scripts like `work.py` — must have the executable bit set.
  Without it, the test runner fails immediately with "Permission denied".
  Run `chmod +x` on each such file after creating it.

- `sb` is launched in the background with a commented-out redirect:

  ```bash
  sb -j 1 -w & # > current_stdout.txt &
  ```

  The test builder strips the `& #` before executing, so the director's reporter output is
  captured in `current_stdout.txt` and compared against `expected_stdout.txt`.

  Every option of `stepup build` can also be given to `sb`, e.g. to raise the log level:

  ```bash
  sb --log-level=INFO -j 1 -w & # > current_stdout.txt &
  ```

- The following `stepup` subcommands are used to interact with the running director
  and to verify the workflow state at well-defined points in time:
    - `stepup wait` — waits until the builder has finished all pending steps.
      `stepup wait -u <file>` / `--update <file>` waits for an update (or creation)
      of `<file>` instead, and `stepup wait -d <file>` / `--delete <file>` waits
      for the deletion of `<file>` instead.
    - `stepup rebuild` — signals the director to start another build phase (used after file changes).
    - `stepup graph <prefix>` — writes the current workflow graph to `<prefix>.txt`,
      which is compared against the corresponding `expected_<prefix>.txt`.
      `stepup wait` or file-update commands are called first to reach a stable state.
    - `stepup join` — waits for the director to shut down and collects its exit code.
    - `stepup shutdown` — asks the director to shut down immediately.
    - `stepup clean ...` — removes StepUp-managed outputs; its output is captured in
      `current_cleanup.txt` and compared against `expected_cleanup.txt`.

- **Simulating file changes between phases** without modifying tracked source files:
  use numbered variants (e.g. `plan1.py`, `plan2.py`) as the committed source files
  and copy them to the working name (`plan.py`) at the appropriate point in `main.sh`.
  Add the working name to `.gitignore` so it is not accidentally committed.
  This pattern applies to any file that needs to differ between phases, not just `plan.py`.

  ```bash
  cp plan1.py plan.py        # first phase
  sb -j 1 -w & # > current_stdout1.txt &
  stepup wait
  ...

  cp plan2.py plan.py        # second phase — triggers a rerun
  stepup wait -u plan.py
  stepup rebuild
  stepup wait
  ```

  The `.gitignore` for such an example should include the generated working name:

  ```text
  .stepup/
  current_*
  plan.py
  ```

- After `stepup join`, wait for the background `stepup build` process and capture its exit code:

  ```bash
  set +e; wait -fn $PID; RETURNCODE=$?; set -e
  ```

  `stepup build` exits with **0** when all steps succeeded,
  and otherwise with a sum of the bits documented in
  [Return Codes](../../docs/reference/returncode.md).
  Never assert a bare number: `example.rc` defines one shell constant per bit
  (`RETURN_CODE_INTERNAL`, `RETURN_CODE_INTERRUPTED`, `RETURN_CODE_FAILED`,
  `RETURN_CODE_WARNING`, `RETURN_CODE_PENDING`, `RETURN_CODE_DRAINED`),
  which say what the example expects instead of leaving the reader to decode a literal.
  `tests/test_conventions.py` keeps them in sync with the `ReturnCode` enum.

    - For tests where all steps must succeed, assert the exit code:

      ```bash
      [[ "${RETURNCODE}" -eq 0 ]] || exit 1
      ```

    - For tests where a step is *expected* to fail, assert the exit code
      and verify the failure via the fail log:

      ```bash
      [[ "${RETURNCODE}" -eq "${RETURN_CODE_FAILED}" ]] || exit 1
      grep "expected error text" .stepup/fail.log
      ```

    - Combine bits with an arithmetic expansion.
      A step that fails without `--keep-going` also drains the scheduler,
      which is the most common combination in these examples:

      ```bash
      [[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_DRAINED)) ]] || exit 1
      ```

      The `[[ ! -f result.txt ]] || exit 1` pattern can confirm that failed steps did
      not produce their outputs.

- Several lines starting with `[[ -f ... ]]` or `[[ ! -f ... ]]` verify that expected
  files are present or absent after a run.

- The **"Standard error" page is not compared verbatim**: the test builder
  (`stepup/core/pytest.py`) replaces its body with `(stripped)` in the captured stdout before
  comparison, because stderr varies across OS and Python versions. To assert specific stderr
  (or stdout) text, grep `.stepup/success.log` from within `main.sh` — it keeps the full,
  un-stripped reporter output, including the standard-error page.

The test builder in `tests/test_examples.py` copies each example to a temporary directory,
applies the `sed` rewrite to `main.sh`, runs it, and compares all `current_*` files against
the corresponding `expected_*` files in the source directory.

Everything `main.sh` writes to stdout and stderr is collected in `main.log`,
which a failing example prints together with the return code of `main.sh`
and the logs under `.stepup/`.
Because examples run under `bash -x`, that log names the command the example died on,
which is what makes an example that stops early (a `stepup` client that could not reach
the director, say) diagnosable from the test output alone.
The environment variable `STEPUP_OVERWRITE_EXPECTED=1` can be set to update the expected
outputs in-place instead of comparing them.
Before using it, create empty placeholder files for every `expected_*` output the example
will produce (e.g. `expected_stdout.txt`, `expected_graph.txt`).
For multi-phase tests, also create the numbered variants
(`expected_stdout1.txt`, `expected_graph1.txt`, etc.).
The overwrite mechanism only writes back files that already exist in the source directory;
without the placeholders, nothing is written.

In some rare cases, the `expected_stdout.txt` is not included because it is not deterministic.
This may happen in examples that require a parallel builder.

A smaller set of examples also has a `test_plan` test in `tests/test_examples.py`,
which runs `plan.py` directly as an ordinary Python script (without StepUp) to verify
that plan scripts do not raise exceptions when executed standalone.
