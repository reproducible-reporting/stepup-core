#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
cp plan1.py plan.py
stepup boot -e -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1


# Run the example
cp plan2.py plan.py
stepup boot -e -j 1 -w > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# Verify that work.py has been skipped.
grep 'SKIP │ ./work.py' current_stdout_2.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
