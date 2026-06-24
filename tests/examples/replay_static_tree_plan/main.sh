#!/usr/bin/env -S bash -x
source ../example.rc

# Run the initial plan.
mkdir -p data
echo hello > data/inp.txt
cp plan1.py plan.py
stepup boot -j 1 -w & # > current_stdout.txt &

# Initial graph.
stepup wait
stepup graph current_graph1

# Check the result.
grep hello out.txt

# Remove static("data/") from the plan, orphaning the static tree.
cp plan2.py plan.py

# Rerun; the static tree is orphaned but out.txt should remain.
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2

# Check the result, should not be affected.
grep hello out.txt

# Restore static("data/") in the plan.
> .stepup/success.log
cp plan1.py plan.py

# Rerun; the consumer should be skipped since data/inp.txt has not changed.
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph3

grep "SKIP │ cp -p data/inp.txt out.txt" .stepup/success.log
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
