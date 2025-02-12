This is an attempt to break the scheduler in StepUp:
two steps declare a static file and then amend each other's static file as input.

This is a perverse example! No one in their right mind would create a workflow like this.
Still, StepUp should be able to do something well-defined with it.

StepUp gracefully handles this seeming deadlock by interrupting and rescheduling (at most)
one of the two steps upon the amend call.
