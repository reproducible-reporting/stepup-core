This example tests that optional steps are correctly managed when their only consumer
lives in a sub-plan rather than in the same plan as the producer.

The top-level plan declares an optional producer step and includes a sub-plan.
Two restart phases are run:

1. The sub-plan consumes the optional producer's output, making it needed (DEFAULT).
   Both the producer and the consumer run.
2. The sub-plan is replaced by a version that no longer consumes that output.
   The optional producer must revert to OPTIONAL and its output must be cleaned up,
   even though the top-level plan (which owns the producer) was not re-executed.
