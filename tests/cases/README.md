# Examples

Each subdirectory of `examples` contains a short example of how to use StepUp.
The script `main.sh` executes the whole example.
Example directories also contain a `README.md` file describing what is being demonstrated.

All examples are included in the test suite,
for which some technicalities are required in the `main.sh` scripts.
These technical details are not relevant for understanding how StepUp works, so you may ignore them:

- To facilitate debugging, the shebang line runs bash in verbose mode: `#!/usr/bin/env -S bash -x`.
- In order to interact with the server (normally done with the keyboard) and to verify results,
  each main script contains blocks of the following form:
  ```bash
  # Comment explaining the interaction.
  python - > current_graph_suffix.txt << EOD
  from stepup.core.api import *
  # Instructions here instead of a comment
  print(graph())
  EOD
  ```
  The corresponding expected output can be found in `expected_graph_{suffix}.txt`
  Before `print(graph())`, there is usually a `wait()` or `watch(...)` to make sure the graph
  is constructed at a well-defined point in time, i.e. after the runner or watcher have completed
  some expected work.
  In the end, a `join()` is used to wait for the runner to complete and to shut down the server.
  For the sake of understanding the examples, you may ignore these extra blocks.
- Several lines of starting with `[[ -f ...` are used to check the presence or absence of files.
- The `main.sh` scripts contain three lines in the beginning:
  - `set -e` will terminate the script when one of the commands fails,
    instead of running until the end.
  - `trap ...` kills the background processes that are still running when the script ends.
    This is cleanup is only needed when the examples fails.
  - `xargs rm -rvf < .gitignore` cleans up outputs from a previous run.
