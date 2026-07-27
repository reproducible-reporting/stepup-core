A build target naming a static file must be rejected the same way whether the database is
fresh or resumed. On a fresh database, this is caught inside `static()`'s RPC call while
`plan.py` runs as an ordinary step, so the build fails cleanly (see `build_target_invalid`).
On a resumed database with an unchanged `plan.py`, `static()` is never called again, so the
same check has to run separately at director startup: `Workflow.reconcile_targets()` raises a
`GraphError`, before the director has even opened its RPC socket. This example demonstrates
that the director contains that error (a short `ERROR` report and a `FAILED` exit code)
instead of crashing with a full traceback, in Run 2.
