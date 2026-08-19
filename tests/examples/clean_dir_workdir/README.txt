The working directory of a step is created before the step runs, even when none of its
outputs is written inside it. When the step disappears from the workflow, the directory is
removed again, provided it is empty by then.
