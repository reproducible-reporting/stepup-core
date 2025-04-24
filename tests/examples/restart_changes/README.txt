When StepUp is restart, it has no idea of which files have changed, so it reruns all steps
and gathers information on changes along the way.
When inputs to steps are unchanged and outputs are also intact, the step is skipped.

This example shows what happens when a few changes are made with respect to `workflow.mpk`.
The new steps are executed and old output files will be removed.
When static (input) path names change, they are never removed.
