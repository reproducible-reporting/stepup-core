#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get graph after normal run.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt

# Change an amended input, rerun, and get the graph after completion of the pending steps.
echo "word2 and some" > inp2.txt
stepup watch-update inp2.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt

# Restart StepUp without changes
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Get graph after restart without changes.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt

# Restart StepUp with changes
echo "word2 and other" > inp2.txt
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout3.txt &

# Get graph after restart without changes.
stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt
