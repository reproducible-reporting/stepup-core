#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. -j 2 lets producer.sh and consumer.py be dispatched concurrently,
# since neither declares a dependency on the other upfront (consumer.py only discovers
# data.txt via amend(), after reading it).
sb -j 2 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

[[ -f data.txt ]] || exit 1
grep hello data.txt

# The consumer step must have been deferred exactly once (unfresh amended input),
# and must converge to SUCCESS without ever failing outright.
grep "Unfresh amended inputs" .stepup/success.log || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1
