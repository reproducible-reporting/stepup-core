#!/usr/bin/env -S bash -x
source ../example.rc

# Run the first plan.
echo a1 > inp1.txt
echo a2 > inp2.txt
sb -j 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
grep a1 out2.txt || exit 1
grep a2 out2.txt || exit 1

# second with a different plan.
rm .stepup/*.log
echo b1 > inp1.txt
echo b2 > inp2.txt
sb -j 1 -w -e & # > current_stdout2.txt &

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
grep b1 out2.txt || exit 1
grep b2 out2.txt || exit 1
