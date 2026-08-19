#!/usr/bin/env -S bash -x
source ../example.rc

# Run 1: a target that names a volatile output must be rejected with a clear GraphError.
# The rejection happens inside define_step (called via run()'s RPC call), so it surfaces as
# an ordinary failure of the plan.py step, just like any other GraphError raised while
# building the graph (e.g. a cyclic dependency).
touch static.txt
cp plan1.py plan.py
sb vol.txt -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ ! -f vol.txt ]] || exit 1
grep -F "A build target cannot be a volatile output: vol.txt" .stepup/fail.log

# Run 2: a target that resolves to a static file must be rejected the same way. plan.py is
# replaced (its content hash changes, so the boot step re-runs and declares the new graph).
rm .stepup/*.log
cp plan2.py plan.py
sb static.txt -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

grep -F "A build target cannot be a static file: static.txt" .stepup/fail.log
