#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example.
stepup boot -j 1 -w & # > current_stdout1.txt &
stepup wait
stepup graph current_graph1
grep hello result.txt

# Change the input file and re-run.
echo "changed" > data.txt
stepup watch-update data.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check that the output reflects the changed input.
grep changed result.txt
