The consumer step reads its input and only then calls amend(), the pattern used by
wrappers like compile_typst.py that can only discover their real inputs after running an
external tool. Since producer.sh and consumer.py are dispatched concurrently (-j 2) with
no declared dependency between them yet, the consumer's first amend() call is flagged
"unfresh" (its own dispatch predates the producer's completion), so it is rescheduled
instead of failing outright. On the next attempt it succeeds. This example does not
include an expected_stdout.txt because the exact interleaving of concurrent output lines
is not deterministic; main.sh instead asserts the specific facts that matter directly.
