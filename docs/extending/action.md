# Custom Actions

A step in StepUp consists of an action (which is a string representing something executable)
and a set of additional arguments that control the execution:
required inputs, outputs, working directory, ...

Many steps use the `runsh` action, which executes a shell command in a subprocess.
However, there are situations where this is not the best option:

- When the overhead of starting a new process is too high.
- If the step should execute Python code
  for which you do not want to create a new command line entry point.

For these situations, you can create a custom action, which consists of two parts:

1. Write a Python function that implements the action:

    ```python
    def custom_action(argstr: str, work_thread: WorkThread) -> int:)
        ...
    ```

    Of course, you should use a more descriptive name than `custom_action`.

    The following conventions are assumed:

    - The action function takes two arguments and returns an integer,
      which is treated as the exit code of the action.
      (Zero means success, non-zero means failure.)
    - The most important argument is `argstr`,
      which contains the part of the string after the action.
      For example, if the step is `runsh echo hello`,
      `argstr` will be `echo hello`.
    - The second argument is a `WorkThread` object,
      which is only used for launching new subprocesses with the `work_thread.runsh()` method.

2. Create an action entry point in `pyproject.toml` pointing to this function:

    ```toml
    [project.entry-points."stepup.actions"]
    custom-action = "your.package:custom_action"
    ```

    where you replace `your.package` with the name of the module that contains `custom_action`.
