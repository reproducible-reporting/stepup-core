# Custom API Functions

This is the most common way to extend StepUp Core.
It is essentially the same as writing functions
as those explained in the [No Rules](../getting_started/no_rules.md) tutorial.
The only difference is that you can include such functions in a Python package,
and call it a StepUp extension, which makes it easier to share and reuse these functions.

A few things to keep in mind:

- API functions that (indirectly) call the [`step()`][stepup.core.api.step] function
  should always return the resulting `StepInfo` object.
- Keep the computational cost of the API function low.
  They should only be used to plan the execution of a step
  and not perform any of the actual work.
- If the step runs a Python script or program, make sure you set `shell=False` in the `step()` call.
  StepUp will then run it in-process via the forkserver when available,
  without spawning a new Python interpreter.
  See [Command Dispatch](command.md) for details.
