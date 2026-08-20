This example tests that supplying a file to a step never takes that file away from its creator.

In the second phase, only the command of the consumer of out.txt changes, so:

- The boot step reruns and detaches everything it created,
  including the sub-plan step, the producer of out.txt and out.txt itself.
- The new consumer cannot be recycled, so plan.py supplies out.txt to a freshly created step
  while out.txt is still detached.
- The sub-plan step is recycled and then skipped, so it never redeclares the producer.

Supplying out.txt must leave it with its producer,
because a detached file whose creator is detached as well belongs to a subtree
that is about to be recycled.
Recreating the file node instead would clear its creator, cut its sources
and invalidate the producer's step hash,
after which out.txt would stay detached forever and all its consumers would remain pending.

The workflow graph must not depend on whether the consumer or the sub-plan
is declared first in plan.py.
