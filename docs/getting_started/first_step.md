# First Step

The goal of the first tutorial is to introduce the basic usage of StepUp.
For the sake of simplicity, a minimal workflow will be defined that does very little.

## Example

Example source files: [`docs/getting_started/first_step/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/first_step)

Create a file `plan.py` with the following contents:

```python
{% include 'getting_started/first_step/plan.py' %}
```

Make this file executable with `chmod +x plan.py`.

1. The first line is required to have the plan executed by the Python 3 interpreter.
2. The second line imports the [`step()`][stepup.core.api.step] function from StepUp Core.
   This module contains functions to communicate with the director process
   of StepUp to define steps and other parts of the workflow.
3. The last line defines a step that writes `Hello World` to the standard output.
   The (first) argument of `step()` is a single string
   that can be interpreted by the default shell, typically `/usr/bin/sh`.
   You may use IO redirection, pipes, and other features supported by the shell.
   (StepUp will not provide any standard input.
   It does capture standard output and error, as shown below.)

In the same directory, run:

```bash
stepup -n 1
```

- The option `-n 1` sets the maximum number of workers to 1, i.e. no parallel execution of steps.

You should see the following output, with colors if your virtual terminal supports them:

```text
{% include 'getting_started/first_step/stdout1.txt' %}
```

Let's analyze the output:

- The first three lines are part of StepUp startup sequence.
  The address `/tmp/stepup-########/director`
  is a [Unix domain socket](https://en.wikipedia.org/wiki/Unix_domain_socket)
  through which the director receives instructions from other processes to define the workflow.
  (The hash signs represent random characters.)
- The `START` and `SUCCESS` lines are shown for steps executed by StepUp:
    - The step `./plan.py` is created by default and runs the script that you just created.
    - Then the step `echo Hello World` is the step defined in `plan.py`.
- When a step produces output, it is shown after the step has completed.
- When no more steps can be executed,
  StepUp checks if it can clean up outdated outpus and then exits.

Now repeat the execution of StepUp with:

```bash
stepup -n 1
```

You will see a slightly different output:

```text
{% include 'getting_started/first_step/stdout2.txt' %}
```

The startup sequence is now a bit longer because StepUp loads the workflow from `.stepup/graph.db`,
which was created in the first run.
It looks for relevant file changes and because `plan.py` has not changed,
it will not rerun it.
Even if file time stamps have changed, it will also check if files have actually changed
by comparing a [Blake2 hash](https://en.wikipedia.org/wiki/BLAKE_(hash_function)#BLAKE2)
of input files, used environment variables and produced outputs.
When you manually remove `.stepup/graph.db`,
StepUp will not know anymore that it already executed some steps and runs all of them again.

## Try the Following

- Change the arguments of the `echo` command in `plan.py` and run `stepup -n 1` again.
  As expected, StepUp detects the change and repeats the `plan.py` and `echo` steps.

- Normally, you would never run `./plan.py` directly as a normal Python script, i.e.,
  without running it through `stepup`.
  Try it anyway, just to see what happens.
  The terminal output shows the commands that would normally be sent to the StepUp director
  process when `plan.py` is executed by `stepup`.
  You should get the following screen output.

{% macro incl() %}
{% include "getting_started/first_step/stdout3.txt" %}
{% endmacro %}

    ```
    {{ incl() | indent(width=4) }}
    ```

    This output contains internal details of StepUp,
    which can be useful for debugging purposes.
