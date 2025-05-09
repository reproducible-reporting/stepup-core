#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan.
echo version1 > inp.txt
stepup boot -n 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version1 out.txt
grep version1 final.txt

# Change  the input and make sure the change is observed, but don't update the output.
echo version2 > inp.txt
stepup watch-update inp.txt
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version1 out.txt
grep version1 final.txt

# Restart StepUp after removing the output.
echo version2 > inp.txt
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version2 out.txt
grep version2 final.txt
