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
  and perform do any of the actual work.
- If the execution consists of calling a Python script,
  you can create a new action instead to improve the performance.
  The action can run the same script without creating a new process.
