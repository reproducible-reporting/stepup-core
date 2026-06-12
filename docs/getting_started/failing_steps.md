# Failing Steps

Developing a StepUp workflow is an interactive process
that often involves encountering failing steps.
These failures will result in screen output containing a bright red `FAIL` message.
Furthermore, a warning message indicating some steps failed will be displayed at the end of the run.

Here, we provide guidance on how to handle these failing steps.

- **Check the error message**:
  The error message usually provides clues about what went wrong.
  If your workflow is very large and the error message scrolls out of view,
  you can always find the full context in the file `.stepup/fail.log`.
  When you open this file in VSCode or PyCharm,
  the file will be automatically reloaded when the workflow is rerun.
  (There is a similar file `.stepup/warning.log` for warnings.)

- **External causes:**
  If the source of the problem is unrelated to STATIC files known to StepUp,
  fixing the problem will not result in a file change that StepUp can detect.
  Therefore, when you restart StepUp from scratch,
  it will rerun all previously failed steps.
  However, when working interactively with `-w` or `-W`,
  failed steps will only rerun if their inputs have changed.
