This example tests a race condition.

The workflow defines a step (work.py) that is created by plan.py.
After plan.py has confirmed through a trigger that work.py has started running, it raises
an error. This causes plan.py to fail and detaches work.py, which is still running (or
about to run) at that point. work.py is not killed: it keeps running to completion on its
own, and is then reported and recorded like any other step, on detached nodes.

See detach_running_skip for the follow-up: a detached step recorded this way is skipped
when its creator is fixed and recreates it in exactly the same way.
