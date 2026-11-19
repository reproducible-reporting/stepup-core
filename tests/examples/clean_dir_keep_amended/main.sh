#!/usr/bin/env -S bash -x
source ../example.rc

# Create the user's files, including a file in a subdirectory of the static tree.
mkdir -p data/sub
echo a > data/sub/a.txt
echo b > data/b.txt

# Run the example, in which the step amends data/sub/a.txt as an input.
sb -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# Remove the user's file, so the step amends data/b.txt instead.
rm data/sub/a.txt
sb -j 1 -w & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# The empty directory of the user is left alone.
[[ -f data/b.txt ]] || exit 1
[[ -d data/sub ]] || exit 1
