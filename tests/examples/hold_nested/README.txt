This example exercises re-entrant hold(): a plan.py declares two batches of steps through a
shared helper function, declare_batch(), which itself opens and closes its own hold() block,
while an outer hold() block is already open around both calls. It checks that no step is
dispatched while any hold() (inner or outer) is still open, even though a job slot is free, and
that once the outer block exits, all three steps -- from both nested batches -- are dispatched
together in _tail_time DESC order (longest first), not declaration order.
