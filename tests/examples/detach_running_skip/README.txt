This example tests that a detached step's result is recorded and can be skipped later.

In the first run, plan.py creates a step (work.py) and then raises an error once
work.py has started, which detaches work.py while it is still running. work.py is not
killed: it runs to completion, and its state, output and step hash are recorded like
those of any other step, on detached nodes.

In the second run, the raise is gone, so plan.py recreates work.py in exactly the same
way. `Trellis.try_recycle` finds the compatible detached step, revives it together with
its products, and the stored hash then lets it be skipped. The counter in out.txt proves
work.py did not run a second time.
