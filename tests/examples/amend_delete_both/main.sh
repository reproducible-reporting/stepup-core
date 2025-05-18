#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Create input files
echo "This is the first half and" > asource1.txt
echo "this is the second half." > asource2.txt

# Run the plan.
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Initial graph
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f asource1.txt ]] || exit 1
[[ -f asource2.txt ]] || exit 1
[[ -f data1.txt ]] || exit 1
[[ -f data2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Remove both intermediates the output and one of the inputs, and restart.
# There should be pending steps because some input is missing.
rm data1.txt data2.txt log.txt asource1.txt
stepup --log-level INFO boot -n 1 -w -e & # > current_stdout2.txt &
PID=$!

# Initial graph
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 4 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f asource1.txt ]] || exit 1
[[ -f asource2.txt ]] || exit 1
[[ ! -f data1.txt ]] || exit 1
[[ -f data2.txt ]] || exit 1
[[ ! -f log.txt ]] || exit 1
