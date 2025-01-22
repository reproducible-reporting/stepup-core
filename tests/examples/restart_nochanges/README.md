When StepUp is restart, it has no idea of which files have changed, so it reruns all steps
and gathers information on changes along the way.
When inputs to steps are unchanged and outputs are also intact, the step is skipped.

This example shows a restart with no file changes.
