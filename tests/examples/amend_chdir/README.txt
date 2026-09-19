Relative paths passed to amend() are relative to the current working directory of the caller,
also when the code runs in another directory than the step itself.
The step's work.py calls amend() in the subdirectory sub/ in two ways:
inside contextlib.chdir() and in a child process started in sub/.
Neither updates the environment variables HERE and ROOT,
so they still refer to the top-level directory.
StepUp computes ${HERE} and ${ROOT} in paths passed to amend() instead,
so they refer to sub/.
The graph must contain the paths under sub/.
