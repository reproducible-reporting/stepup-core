A step can start to depend on `STEPUP_DIRECTOR_I` while it runs, with `getenv()`.
It then runs at every start of the director, although `plan.py` does not mention the variable.
The value that the step gets is the value that StepUp stores and hashes,
also when the step runs in the forkserver.
