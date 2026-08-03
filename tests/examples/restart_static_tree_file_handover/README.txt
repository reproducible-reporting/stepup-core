Runs the static_tree_file_handover scenario, then restarts with nothing changed.

The hand-over from step to tree must be a direct creator reassignment, not a detach/
recycle through Trellis.create(): that would call Step.after_lost_product() on plan.py, deleting
its stored hash and making it rerun on every restart, forever. This example checks that a
real restart skips ./plan.py and produces a byte-identical graph, rather than only checking
the hand-over one level down in a unit test.
