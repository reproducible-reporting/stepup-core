This example verifies that StepUp can recycle a step while it is running,
and once more after that step has completed.
The driver defers itself twice, so it declares the same work step three times.
The steps that wake up the deferred driver are created by the plan, not by the driver itself,
because the steps created by a deferred step are never scheduled.
