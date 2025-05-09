#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f 'plan.py' ]] || exit 1
[[ -f 'call is cool.py' ]] || exit 1
[[ -f 'script is cool.py' ]] || exit 1
[[ -f 'call is cool_out.json' ]] || exit 1
[[ -f 'the road to hell is paved with whitespace.txt' ]] || exit 1
[[ -f 'white space leaves a lot of room for mistakes.txt' ]] || exit 1
