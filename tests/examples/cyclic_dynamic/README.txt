This is an attempt to break the scheduler in StepUp:
two steps declare an echo step and then amend the output of the other one's echo step as input.

This is a perverse example! No one in their right mind would create a workflow like this.
Still, StepUp should be able to do something well-defined with it.

StepUp 3 still executed all steps of this example.
StepUp 4 has become a bit more strict and refuses to complete this workflow,
as it checks more carefully to not start steps created by other steps that
have not SUCCEEDED (or are still RUNNING).
