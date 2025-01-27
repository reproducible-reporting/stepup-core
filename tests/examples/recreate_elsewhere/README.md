This example tests a corner case, consisting of two steps:

1. A first plan runs two steps, of which the first creates the second.
2. A second step runs a different plan, with the same inner step, but a different inner one.

The difficulty is that the inner step will still be attached to its orphaned creator.
When it is recreated, it needs to be detached first.
