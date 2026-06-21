#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

echo "content" > input.txt

# First run: resource is available, step executes and its stored hash is set.
export STEPUP_BOOT_RESOURCES="limited:1"
stepup boot -j 1 -w & # > current_stdout1.txt &
stepup wait
stepup graph current_graph1

[[ -f output.txt ]] || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1

# Change input.txt to trigger a hash change. Notify the director so the watcher
# queues the update. At shutdown the watcher processes the change: the step is
# made PENDING while its stored hash (= H(original content)) is preserved.
echo "changed" > input.txt
stepup watch-update input.txt
stepup join
wait

[[ ! -f .stepup/fail.log ]] || exit 1

# Restore the original content NOW (after the director has shut down).
# The step is PENDING in the database with a stored hash that matches "content".
# On the next startup, scan_file_changes will detect the mismatch between the
# stored hash (H("changed")) and the current file (H("content")), updating the
# file hash in the DB, but leaving the step PENDING.
echo "content" > input.txt

# Second run: no resource available. The step is PENDING with a stored hash
# whose inp_digest matches the current input (both "content"). It should be
# SKIPPED via the CHECKING state — no resource slot is needed for the hash check.
unset STEPUP_BOOT_RESOURCES
rm -f .stepup/director.log .stepup/success.log
stepup boot -j 1 -w & # > current_stdout2.txt &
stepup wait
stepup graph current_graph2
stepup join
wait

[[ -f output.txt ]] || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1
grep "SKIP" .stepup/success.log
