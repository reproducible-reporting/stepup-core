A plan step in a subdirectory declares data.txt static and then reads it,
so data.txt is a product of that plan step and a dynamic input of the same step.
The subdirectory is dropped from the top-level plan and then added back again.

This creator/dependency cycle survives the cleanup of the detached subdirectory:
neither the plan step nor data.txt can be deleted, while the other products are.
Because the plan step lost products, its hash is dropped by Trellis.delete_detached,
so it is rerun instead of skipped when the subdirectory is added back,
which reproduces sub/copy.txt.
