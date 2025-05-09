#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan for the first time.
mkdir -p static
echo foo > static/foo.txt
echo bar > static/bar.txt
cp plan1.py plan.py
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/foo.txt ]] || exit 1
[[ -f foo.txt ]] || exit 1
[[ -f static/bar.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
grep foo foo.txt
grep bar bar.txt

# Remove foo, change to plan2.py and run again
rm .stepup/*.log
rm foo.txt
rm static/foo.txt
echo other > static/bar.txt
cp plan2.py plan.py
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f static/foo.txt ]] || exit 1
[[ ! -f foo.txt ]] || exit 1
[[ -f static/bar.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
grep other bar.txt
