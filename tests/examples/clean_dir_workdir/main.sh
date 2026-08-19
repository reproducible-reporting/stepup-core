#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with plan1, which has a step with a working directory.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# StepUp created the working directory, even though no output is written in it.
[[ -f keep.txt ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ -d sub ]] || exit 1

# Run the example with plan2, which no longer has the step with a working directory.
cp plan2.py plan.py
sb -j 1 -w & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# The working directory is empty and goes away with its step.
[[ -f keep.txt ]] || exit 1
[[ ! -f hello.txt ]] || exit 1
[[ ! -d sub ]] || exit 1
