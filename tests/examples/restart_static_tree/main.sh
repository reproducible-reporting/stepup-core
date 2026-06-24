#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan for the first time.
mkdir -p static
echo first > static/foo.txt
stepup boot -j 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
grep first static/foo.txt
grep first bar.txt

# Run the plan again without any changes.
rm .stepup/*.log
stepup boot -j 1 -w -e & # > current_stdout2.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
grep first static/foo.txt
grep first bar.txt

# Run the plan again with changes.
echo second > static/foo.txt
rm .stepup/*.log
stepup boot -j 1 -w -e & # > current_stdout3.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
grep second static/foo.txt
grep second bar.txt
