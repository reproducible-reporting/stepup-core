#!/usr/bin/env -S bash -x
source ../example.rc

# Create the user's file in a directory that is not declared static.
mkdir notes
echo a > notes/a.txt

# Run the example with plan1, which declares the file static.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# Remove the user's file and switch to plan2, which no longer declares it.
rm notes/a.txt
cp plan2.py plan.py
sb -j 1 -w & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# The empty directory of the user is left alone.
[[ -d notes ]] || exit 1
