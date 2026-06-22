#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with factor=2.
stepup boot -j 1 -w & # > current_stdout1.txt &
stepup wait
stepup graph current_graph1

# Verify factor=2 output (two lines).
[[ $(wc -l < result.txt) -eq 2 ]] || exit 1

# Change config to factor=3 and re-run.
echo '{"factor": 3}' > config.json
stepup watch-update config.json
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Verify factor=3 output (three lines).
[[ $(wc -l < result.txt) -eq 3 ]] || exit 1
