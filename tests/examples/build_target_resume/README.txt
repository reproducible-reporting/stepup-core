`Workflow.reconcile_targets()` validates targets against a loaded graph and flags affected
steps for recompute, since declaration-time validation only runs when steps are (re)declared,
which does not happen for a database-resumed run against an unchanged `plan.py`. This example
demonstrates that startup reconciliation for an exact-file target (mirroring
`build_target_dir_resume`, but for an exact target instead of a directory target):
`input.txt` (shared by `wanted.txt` and `other.txt`) changes while `plan.py` stays unchanged,
so only the reconciliation pass picks up `wanted.txt`'s producer step again. `other.txt`,
which is not targeted, stays unbuilt even though its (shared) input also changed. A further
run then edits `plan.py` (a comment only) to show that the same elevation also happens when
the step is redeclared via `Step.recycle()` instead of a fresh declaration.
