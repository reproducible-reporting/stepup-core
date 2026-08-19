This example exercises hold()/release(): a plan.py declares three steps of increasing
duration, in increasing (i.e. declaration-order-is-wrong) order, inside a hold() block.
It checks that none of them is dispatched while still held, even though a job slot is free,
and that once released, they are dispatched in _tail_time DESC order (longest first), not
declaration order.
