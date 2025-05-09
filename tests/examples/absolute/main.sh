#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare
export mytmpdir=$(mktemp -d)
trap 'rm -rf $tmpdir' EXIT
PATH_SRC="$mytmpdir/this_is_potentially_unsafe_18731"
PATH_DST="$mytmpdir/this_is_potentially_unsafe_79824"
echo hello > $PATH_SRC
rm -rf $PATH_DST

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep hello $PATH_SRC
grep hello $PATH_DST

# Modify absolute path and rerun
echo changed > $PATH_SRC
stepup watch-update $PATH_SRC
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep changed $PATH_SRC
grep changed $PATH_DST
