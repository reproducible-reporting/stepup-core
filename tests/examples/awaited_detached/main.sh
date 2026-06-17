#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout_1.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 4 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1

# Run the example
cp plan2.py plan.py
stepup boot -n 1 -w > current_stdout_2.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 4 ]] || exit 1

# Check that nothing related to src1.txt is present in the standard output.
# (Return code of grep must be explicitly ignored.)
NUM_SRC1=$(grep -c "src1.txt" current_stdout_2.txt || true)
[[ "${NUM_SRC1}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
