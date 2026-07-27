A build target matching a stale VOLATILE or STATIC `File` row left over from a previous
plan.py declaration must not block a legitimate replan that redeclares it as a regular
output. `reconcile_targets()`'s creator-chain guard stays silent whenever a step in the
row's creator chain is PENDING (i.e. about to redeclare it), instead of raising a
`GraphError`. This example changes out.txt from a `vol_path` to a regular output between two
runs, targeting it in the second run.
