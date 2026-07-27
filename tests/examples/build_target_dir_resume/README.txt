`Workflow.reconcile_targets()` validates targets against a loaded graph and flags affected
steps for recompute, since declaration-time validation only runs when steps are (re)declared,
which does not happen for a database-resumed run against an unchanged `plan.py`. This example
demonstrates the directory-target half of that startup reconciliation: `a_input.txt` changes
while `plan.py` stays unchanged, so only the bulk range `UPDATE` added to
`reconcile_targets()` for directory targets can pick up `out/a.txt`'s producer step again.
`other.txt`, outside the targeted directory, stays unbuilt even though its (shared) input
also changed, showing that directory-target scoping still applies on a resumed database.
