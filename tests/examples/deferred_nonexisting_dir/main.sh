#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
mkdir sub/
echo hello > sub/message.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/message.txt ]] || exit 1
[[ -f message.txt ]] || exit 1

# Create the required static files and try again
rm sub/message.txt
sleep 0.1
rmdir sub/
sleep 0.1
rm message.txt
stepup watch-delete sub/
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f sub/message.txt ]] || exit 1
[[ ! -f message.txt ]] || exit 1

# Create again and rerun
mkdir sub/
echo hello > sub/message.txt

stepup watch-update sub/message.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/message.txt ]] || exit 1
[[ -f message.txt ]] || exit 1
