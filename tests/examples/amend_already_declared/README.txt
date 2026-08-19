A step may amend information that its plan already declared for it.

Declaring a dynamic input up front makes for better scheduling: the step is not dispatched
before the input is available, so it does not have to be deferred and rerun. The step itself
should not have to know what was declared for it, so amending an input, environment variable,
output or volatile output that is already known is silently ignored.

Such an amendment adds nothing to the graph, and the graph shows that: none of the four
is marked [dynamic], exactly as if work.py had not called amend() at all.

Amendments never cross the boundary between the four arguments, though: amending a declared
output as volatile (or vice versa) remains an error.
