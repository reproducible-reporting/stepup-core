Exercises the director-restart case for the start/stop-timestamp freshness check.

In phase 1, source.sh and sink.py race once (unfresh defer) and converge.
In phase 2, the director is restarted with a fresh (empty) Scheduler;
source.sh stays SUCCEEDED from phase 1 and is never re-dispatched,
so it has no start_times/stop_times entry in the new invocation.
sink.py is forced to re-run via an edit to its ordinary trigger.txt input,
and its renewed amend(inp=["data.txt"]) call must be accepted immediately,
proving that a missing stop_times entry after a restart is treated as "no race possible",
not as a spurious block.

This example does not include an expected_stdout*.txt because the exact interleaving
of concurrent output lines in phase 1 is not deterministic;
main.sh instead asserts the specific facts that matter directly.
