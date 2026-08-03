Exercises the liveness gap fixed by differentiating between unavailable and unfresh inputs.

The sink's amend() call happens after its producer has already fully completed,
so the dependency edge to data.txt is created only after source.sh's own File.completed() already ran.
Under the old single-bucket scheme nothing would ever call mark_pending() on this step again,
so it would sit in PENDING forever.
Here it converges to SUCCESS after deferring exactly once,
with a single "stepup wait" and no watch-update/run push cycle,
proving the self-resolving unfresh path works without needing one.
This example does not include an expected_stdout.txt
because the exact interleaving of concurrent output lines is not deterministic;
main.sh instead asserts the specific facts that matter directly.
