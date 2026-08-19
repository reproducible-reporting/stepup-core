This example tests that optional steps are correctly managed when their only sink
lives in a sub-plan rather than in the same plan as the producer.

The top-level plan declares an optional producer step and includes a sub-plan.
Three restart phases are run:

1. The sub-plan consumes the optional producer's output, making it needed (DEFAULT).
   Both the producer and the sink run.
2. The sub-plan is replaced by a version that no longer consumes that output.
   The optional producer must revert to OPTIONAL and its output must be cleaned up,
   even though the top-level plan (which owns the producer) was not re-executed.
3. The sub-plan consumes the output again, and the producer must be needed again.
   The top-level plan is still not re-executed, so the producer is never redeclared.
   Its reverted output node is what carries the need back to it, through the
   dependency edge the new consumer attaches to, so that node has to survive
   phase 2 rather than be deleted along with the file on disk.
