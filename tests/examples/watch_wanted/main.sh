#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
touch static.txt
touch input1.txt
touch input2.txt
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1

# Remove plan and rerun.
rm plan.py
stepup watch-delete plan.py
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1

# Replace plan and rerun.
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1
