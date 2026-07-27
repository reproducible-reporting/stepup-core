#!/usr/bin/env -S bash -x
source ../example.rc

echo hello > input.txt

# Run 1: full untargeted build with out.txt declared as a vol_path.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f out.txt ]] || exit 1

# Run 2: plan.py changes so out.txt becomes a regular output instead, and the build now
# targets out.txt directly. At the moment reconcile_targets() runs (right after startup
# scanning, before the replan actually executes), the database still holds the stale
# VOLATILE row from run 1. The creator-chain guard must recognize that plan.py's step is
# PENDING (its content changed) and therefore stay silent instead of raising -- the
# replan is legitimate and about to redeclare out.txt as a regular output.
rm .stepup/*.log
cp plan2.py plan.py
sb out.txt -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

[[ -f out.txt ]] || exit 1
grep -q hello out.txt
[[ ! -f .stepup/fail.log ]] || exit 1
