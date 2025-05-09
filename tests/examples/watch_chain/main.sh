#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
cp config1.json config.json
stepup --log-level INFO boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f script.log ]] || exit 1
grep script.log report.txt
grep script.log copy.txt
[[ -f output.txt ]] || exit 1

# Modify config.json and rerun.
cp config2.json config.json
stepup watch-update config.json
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f script.log ]] || exit 1
[[ -f other.log ]] || exit 1
grep other.log report.txt
grep other.log copy.txt
[[ -f output.txt ]] || exit 1
