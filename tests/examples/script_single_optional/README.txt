The `optional=True` of the script function should only make the "run part"
of the script optional. The planning should be executed anyway,
to inform stepup which outputs the run part may create.
Through this information, StepUp can decide if the output is needed
by other steps or not.

This example first shows a script with an optional run part that is not needed.
Then the plan is modified to make it required.
In the second case, the script's run part must be executed.
