This example shows how a step collects the dependencies amended by a child process.

The step work.py starts child/child.py in a child process,
which amends its own dependencies.
These amendments are not visible to get_amended_info() in work.py,
because it only covers the amend() calls of the current process.
The child therefore reports its own get_amended_info() as JSON on its standard output.
The step combines the declared dependencies from get_info()
with those amended by itself and by the child, using StepInfo.merge(),
which rebases the paths of the child from child/ onto the working directory of the step.

In current_all_info.json, child/data.txt and child/result.txt come from the child,
extra.txt from the step itself, and the other paths from the plan.
