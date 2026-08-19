This example tests that a detached, still-running step can still amend itself.

Like error_detach_running, the workflow defines a step (work.py) that is created by
plan.py. After plan.py has confirmed through a trigger that work.py has started
running, it raises an error, which causes plan.py to fail and detaches work.py while
it is still running.

Unlike error_detach_running, work.py itself calls amend() after being detached
(simulating the automatic amend() call every Python step makes, e.g. via getenv() or
the end-of-run import-tracking amend()). Detachment is about provenance, not liveness,
so the call is carried out as usual: work.py carries on and writes late.txt.

The amended dependency on extra_input.txt is recorded in the graph, as is work.py's
output, both as detached nodes: a detached creator only ever creates detached
products. This is what allows work.py to be skipped when plan.py is fixed and
recreates it in exactly the same way.
