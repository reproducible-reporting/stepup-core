Runs the static_tree_file_handover scenario, then restarts with nothing changed,
then restarts twice more with a changed plan.py, which forces ./plan.py to rerun.

The hand-over from step to tree must be a direct creator reassignment, not a detach/
recycle through Trellis.create(): that would call Step.after_lost_product() on plan.py, deleting
its stored hash and making it rerun on every restart, forever. This example checks that a
real restart skips ./plan.py and produces a byte-identical graph, rather than only checking
the hand-over one level down in a unit test.

The last two runs cover what the second one cannot, precisely because ./plan.py is
skipped there: the declarations are replayed against a graph that already contains the
tree and the file under it, once in each declaration order.

The third run (plan2.py) declares the tree before the file, so registering the tree
re-adopts the file's detached node and the static() call naming that file must recognize
it as already declared. Without that check, it recreates an already attached node and the
run dies with "Node (file:src/foo.txt) already exists and is not detached."

The fourth run returns to plan1.py, where the file is declared while no tree exists yet,
so the hand-over of the first run is repeated on a graph that has seen it before.
