#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export ENV_VAR_TEST_STEPUP_IDX="3"
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Rerun without changes
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1

# Restart with a different environment variables
export ENV_VAR_TEST_STEPUP_IDX="1"
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f converted1.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw1 used.txt
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted3.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1


# Restart with the original environment variable.
export ENV_VAR_TEST_STEPUP_IDX="3"
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout3.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1
