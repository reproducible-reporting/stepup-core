This example is modeled on cyclic_dynamic, but constructed with hold() as a single
self-referential step instead of two cooperating scripts.

plan.py opens a hold() block, declares a step that produces inp1.txt, and then calls
amend(inp="inp1.txt") while still inside that same hold() block. Without the
AmendWhileHoldingError guard, this would deadlock: the held step cannot be dispatched until
the hold is released, but plan.py cannot release the hold (it is blocked in amend()) until
inp1.txt becomes available.

Instead, amend() must raise AmendWhileHoldingError immediately, since the calling step
(plan.py) itself has an open hold(). This causes plan.py to fail fast with a clear error,
rather than ending in a "N step(s) remained pending" warning the way cyclic_dynamic does.
