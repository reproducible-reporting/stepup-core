This example tests that a recreated optional step is still activated
when its sink is two steps deep in a subplan that is not re-executed.

The top-level plan declares an optional producer of hop2.txt and includes a subplan.
The subplan consumes hop2.txt in a chain of two steps (hop3.txt and hop4.txt).

In the second phase, only the top-level plan changes:
the command of the optional step is modified, so the old step node is discarded
and a fresh one is created. The subplan is unchanged and is therefore skipped,
i.e. its steps are recycled without any of their dependencies being redeclared.
The recreated optional step must still be recognized as needed (implied DEFAULT)
and rerun, so that the whole chain in the subplan can be completed.
