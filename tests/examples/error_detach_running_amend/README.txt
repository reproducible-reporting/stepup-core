This example tests the early abort of a detached, still-running step.

Like error_detach_running, the workflow defines a step (work.py) that is created by
plan.py. After plan.py has confirmed through a trigger that work.py has started
running, it raises an error, which causes plan.py to fail and detaches work.py while
it is still running.

Unlike error_detach_running, work.py itself calls amend() after being detached
(simulating the automatic amend() call every Python step makes, e.g. via getenv() or
the end-of-run import-tracking amend()). `DirectorHandler.amend()` forces
`keep_going = False` for a detached step, so this call raises `InputNotFoundError`
inside work.py, aborting it before it can write late.txt. This confirms that a
detached-but-running step is made to abort early instead of running to completion
pointlessly.

Note that the traceback itself is not observable in any log: `Executor.report()`
deliberately blanks a detached run's stderr, since its raw result (success or failure)
is considered moot once detached. The absence of late.txt is therefore the only
observable signal that the abort happened.
