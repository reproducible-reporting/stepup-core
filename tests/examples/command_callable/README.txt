The command of a step may be a callable that builds the command text from the step's own
paths, so a path list is written only once instead of being assigned to a variable first.
The callable declares any subset of the parameters inp, out and vol.
Note that the auto-detected local executable (./gen.py) is an input of the first step, yet it
is not passed to the callable: it already appears as the first word of the command itself.
