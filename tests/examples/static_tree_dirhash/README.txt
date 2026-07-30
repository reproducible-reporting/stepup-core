A directory inside a static tree, used as if it were a file input of a step.
StepUp cannot compute the file hash of a directory,
so the input is rejected at the run() call in plan.py,
which fails with a traceback pointing at the offending line.
Because the input never enters the workflow,
the startup phase of the second invocation has nothing to hash either.
