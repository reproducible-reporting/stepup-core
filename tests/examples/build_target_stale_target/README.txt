A build target matching a step whose `_implied_need` is a stale `TARGET` value -- left
behind by a previous run that targeted it -- must not cause that step to be dispatched again
just because one of its inputs changed. `Workflow.reconcile_targets()` resets any stale
`TARGET` row by flagging it for a `_check_after` recheck on every run. This example targets
`a.txt` once so its step's `_implied_need` is left at `TARGET`, then, in a later run, changes
both `a_input.txt` and `b_input.txt` but targets only `b.txt`: `b.txt` is rebuilt, while
`a.txt` -- no longer a target -- is not, even though its input also changed and its step
would otherwise be PENDING.
