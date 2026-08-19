A mistake in `plan.py` (a cyclic dependency between two copies) is reported with a short
traceback: the user's own call site, the exception, and nothing else.
The director-side traceback and StepUp's own frames are left out, because the error is one
the user can fix by changing `plan.py`.
They are not thrown away: the director writes them to `.stepup/director.log`.

The counterpart of this example is `error_debug_traceback`, which shows what STEPUP_DEBUG=1
restores.
