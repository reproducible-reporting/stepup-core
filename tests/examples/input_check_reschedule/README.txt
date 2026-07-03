This example pins down the priority between two step-completion mechanisms
when both trigger on the same dispatch: a step that requests a reschedule
via amend() of an unavailable input, and that same step tampering with one
of its own already-declared, already-hashed inputs.

A step that corrupts its own declared input should arguably be failed
immediately, not rescheduled. main.sh asserts that desired outcome, so a
failing main.sh here documents a known gap in stepup/core/executor.py's
execute_job (wants_reschedule is checked before run.success), not an
environment problem.
