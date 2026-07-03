A variant of the reschedule_cap example where the step catches and swallows
amend()'s InputNotFoundError instead of letting it propagate, violating the
documented contract ("let this exception propagate — do not catch it"), and
exits 0 anyway.

Regression test for the diagnostic page that must still explain why a
cap-exceeded step FAILED, even when the step's own exit code is 0: the FAIL
banner and exit code are already correct in this case, but the "Rescheduled
more than N times" page must not be silently dropped.
