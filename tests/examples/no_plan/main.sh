#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
stepup boot -j 1 -w & # > current_stdout.txt &

# Wait for background processes, if any.
wait
