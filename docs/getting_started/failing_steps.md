# Failing Steps

The development of a StepUp workflow is an interactive process,
where it is common to have failing steps.
These will result in screen output with a bright red `FAIL` message.
At the end of a run, a warning message will be displayed that some steps have failed.

Here, we provide some guidance on how to handle failing steps.

- **Check the error message**:
  The error message will often provide a clue to what went wrong.
  If your workflow is so large that the error message has scrolled out of view,
  you can always find the error message in the file `.stepup/fail.log`.
  When you open this file in VSCode or PyCharm,
  the file will be automatically reloaded when the workflow is rerun.
  (There is a similar file `.stepup/warning.log` for warnings.)

- **External causes:**
  If the source of the problem is unrelated to STATIC files known to StepUp,
  fixing the problem will not result in a file change that StepUp can detect.
  Therefore, when you restart StepUp from scratch,
  it will rerun all previously failed steps.
  When working interactively with `-w` or `-W`,
  failed steps will only rerun if their inputs have changed.
