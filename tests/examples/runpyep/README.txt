This example demonstrates the runpyep action.

When the first word of a run() command is a bare name matching a console_scripts
entry point in the current Python environment, StepUp automatically selects the
runpyep action, which runs the entry point in-process via the forkserver when
available, rather than spawning a new subprocess.

Here, `stepup show-config` is used as the command. The `stepup` command is a
console_script entry point, so it is detected as runpyep.
