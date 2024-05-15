# First Step

The goal of the first tutorial is to introduce the basic usage of StepUp.
For the sake of simplicity, a minimal workflow will be defined that does not achieve much.


## Example

Example source files: [getting_started/first_step/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/first_step)

Create a file `plan.py` with the following contents:

```python
{% include 'getting_started/first_step/plan.py' %}
```

Make this file executable with `chmod +x plan.py`.

1. The first line is required to have the plan executed by the Python interpreter.
2. The second line imports the [step()][stepup.core.api.step] function from StepUp Core.
   This module contains functions to communicate with the director process
   of StepUp to define steps and other parts of the workflow.
3. The last line defines a step that writes `Hello World` to the standard output.

In the same directory, run:

```bash
stepup -n -w1
```

- The option `-n` will execute the plan non-interactively, to keep things simple.
- The option `-w1` sets the maximum number of workers to 1, i.e. no parallel execution of steps.

You should see the following output, with colors if your virtual terminal supports them:

```txt
{% include 'getting_started/first_step/stdout1.txt' %}
```

Let's analyze the output:

- The first three lines are part of StepUp startup sequence.
  The address `/tmp/stepup-########/director` is a [Unix domain socket](https://en.wikipedia.org/wiki/Unix_domain_socket) through which the director receives instructions from other processes to define the workflow.
  (The hash signs represent random characters.)
- The `START` and `SUCCESS` lines are shown for steps executed by StepUp:
    - The step `./plan.py` is created by default and runs the script that you just created.
    - Then the step `echo Hello World` is the step defined in `plan.py`.
- When a step produces output, it is shown after the step has completed.
- When no more steps can be executed, StepUp wraps up by saving the worklow for future runs.
- Because of the `-n` option, StepUp immediately shuts down.

Now repeat the execution of StepUp with:

```bash
stepup -n -w1
```

You will see a slightly different output:

```txt
{% include 'getting_started/first_step/stdout2.txt' %}
```

The steps are skipped (no longer executed) because their inputs have not changed.
This is achieved by loading the file `.stepup/workflow.mpk.xz`, which contains the state of the
most recent execution of all steps.
StepUp determines if a step can be skipped by comparing a [Blake2 hash](https://en.wikipedia.org/wiki/BLAKE_(hash_function)#BLAKE2) including inputs, used environment variables and produced outputs.
When you manually remove `.stepup/workflow.mpk.xz`,
StepUp will not know anymore that it already executed some steps and runs all of them again.


## Try the Following

- Change the arguments of the `echo` command in `plan.py` and run `stepup -n -w1` again.
  As expected, StepUp detects the change and repeats the `plan.py` and `echo` steps.

- Normally, you would never run `./plan.py` directly as a normal Python script, i.e.,
  without running it through `stepup`.
  Try it anyway, just to see what happens.
  The terminal output shows the commands that would normally be sent to the StepUp director
  process when `plan.py` is executed by `stepup`.
  You should get the following screen output:

    ```
    {% include 'getting_started/first_step/stdout3.txt' | indent(width=4) %}
    ```

    This output contains internal details of StepUp,
    which can be useful for debugging purposes.
