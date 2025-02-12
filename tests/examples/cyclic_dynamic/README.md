This is an attempt to break the scheduler in StepUp:
two steps declare an echo step and then amend the output of the other one's echo step as input.

This is a perverse example! No one in their right mind would create a workflow like this.
Still, StepUp should be able to do something well-defined with it.

StepUp gracefully handles this seeming deadlock by interrupting and rescheduling both steps upon the amend call.
