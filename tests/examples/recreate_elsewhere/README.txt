This example tests a corner case, consisting of two steps:

1. A first plan1.py runs two steps, of which the first creates the second.
2. A second plan2.py runs two related steps, with the same inner step, but a different outer one.

The difficulty is that the inner step will still be attached to its orphaned creator.
When it is recreated, it needs to be detached first.
