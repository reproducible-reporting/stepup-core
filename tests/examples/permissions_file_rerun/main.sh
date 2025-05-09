#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with specific permissions on the input.
chmod +x input.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
stat input.txt | grep 'Access: (0755/-rwxr-xr-x)'
stat output.txt | grep 'Access: (0755/-rwxr-xr-x)'

# Rerun the plan with different permissions for the input.
chmod -x input.txt

# Wait and get graph.
stepup watch-update input.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
stat input.txt | grep 'Access: (0644/-rw-r--r--)'
stat output.txt | grep 'Access: (0644/-rw-r--r--)'
