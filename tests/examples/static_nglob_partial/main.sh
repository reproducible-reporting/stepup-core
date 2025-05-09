#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare static inputs
echo "Not" > head_ignored.txt
echo "matching" > tail_ignored.txt
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt

# Run the example
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1

# Modify nglob results and rerun
echo "ty" > tail_y.txt
echo "hz" > head_z.txt
stepup watch-update tail_y.txt
stepup watch-update head_z.txt
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
[[ ! -f paste_z.txt ]] || exit 1

# Modify nglob results and rerun
rm tail_y.txt
stepup watch-delete tail_y.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1
[[ ! -f paste_z.txt ]] || exit 1

# Modify nglob results and restart
echo "ty" > tail_y.txt
echo "tz" > tail_z.txt
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
grep 'hz tz' paste_z.txt
