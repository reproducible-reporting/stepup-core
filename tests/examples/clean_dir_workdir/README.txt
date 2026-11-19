The working directory of a step is created before the step runs,
even when none of its outputs is written inside it.
When the step disappears from the workflow, the directory is kept,
because StepUp only removes the directory of one of its own outputs.
