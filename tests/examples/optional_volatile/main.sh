#!/usr/bin/env -S bash -x
source ../example.rc

# Phase 1: the consumer makes the optional producer needed, so it runs.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph1

# Both outputs were created.
[[ -f out.txt ]] || exit 1
[[ -f vol.log ]] || exit 1

# Phase 2: drop the consumer, so the producer is reverted to PENDING.
cp plan2.py plan.py
stepup wait -u plan.py
stepup rebuild
stepup wait
stepup graph current_graph2

# Both outputs are removed from disk.
# In the graph, out.txt is back to PLANNED while vol.log stays VOLATILE,
# because VOLATILE is the only state in its role.
[[ ! -f out.txt ]] || exit 1
[[ ! -f vol.log ]] || exit 1

# Phase 3: restore the consumer, so the producer becomes needed again
# without being redeclared, and recreates both outputs.
cp plan1.py plan.py
stepup wait -u plan.py
stepup rebuild
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f vol.log ]] || exit 1
