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

- To facilitate debugging, the shebang line runs bash in verbose mode: `#!/usr/bin/env -S bash -x`.

- Each `main.sh` starts with three boilerplate lines:
    - `set -e` terminates the script as soon as any command fails.
    - `trap ...` kills background processes when the script exits,
      which only matters when the example fails.
    - `rm -rvf $(cat .gitignore)` cleans up outputs from a previous run.

- `stepup boot` is launched in the background with a commented-out redirect:

  ```bash
  stepup boot -j 1 -w & # > current_stdout.txt &
  ```

  The test builder strips the `& #` before executing, so the director's reporter output is
  captured in `current_stdout.txt` and compared against `expected_stdout.txt`.

- The following `stepup` subcommands are used to interact with the running director
  and to verify the workflow state at well-defined points in time:
    - `stepup wait` — waits until the builder has finished all pending steps.
    - `stepup run` — signals the director to start another run cycle (used after file changes).
    - `stepup graph <prefix>` — writes the current workflow graph to `<prefix>.txt`,
      which is compared against the corresponding `expected_<prefix>.txt`.
      `stepup wait` or file-update commands are called first to reach a stable state.
    - `stepup join` — waits for the director to shut down and collects its exit code.
    - `stepup shutdown` — asks the director to shut down immediately.
    - `stepup watch-update <file>` — notifies the director that a file has been updated.
    - `stepup watch-delete <file>` — notifies the director that a file has been deleted.
    - `stepup clean ...` — removes StepUp-managed outputs; its output is captured in
      `current_cleanup.txt` and compared against `expected_cleanup.txt`.

- After `stepup join`, a bare `wait` collects any remaining background processes.

- Several lines starting with `[[ -f ... ]]` or `[[ ! -f ... ]]` verify that expected
  files are present or absent after a run.

The test builder in `tests/test_examples.py` copies each example to a temporary directory,
applies the `sed` rewrite to `main.sh`, runs it, and compares all `current_*` files against
the corresponding `expected_*` files in the source directory.
The environment variable `STEPUP_OVERWRITE_EXPECTED=1` can be set to update the expected
outputs in-place instead of comparing them.

In some rare cases, the `expected_stdout.txt` is not included because it is not deterministic.
This may happen in examples that require a parallel builder.

A smaller set of examples also has a `test_plan` test in `tests/test_examples.py`,
which runs `plan.py` directly as an ordinary Python script (without StepUp) to verify
that plan scripts do not raise exceptions when executed standalone.
