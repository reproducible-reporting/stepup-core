StepUp scripts can be distributed over multiple directories.
All paths are internally translated to the working directory of the StepUp server.
The command in a step is executed in the current directory `step(...)`
or in a workdirectory given to the `step` function.
