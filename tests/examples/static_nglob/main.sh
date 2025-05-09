#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Copy sec-2-2.txt in place
cp sec-2-2.txt ch-2-theory/sec-2-2-advanced.txt

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ch-1-intro/sec-1-1-blabla.md ]] || exit 1
[[ -f ch-1-intro/sec-1-2-some-more.md ]] || exit 1
[[ -f ch-1-intro/ch-1-compiled.md ]] || exit 1
[[ -f ch-2-theory/sec-2-1-basics.md ]] || exit 1
[[ -f ch-2-theory/sec-2-2-advanced.md ]] || exit 1
[[ -f ch-2-theory/ch-2-compiled.md ]] || exit 1
[[ -f ch-3-conclusions/sec-3-1-summary.md ]] || exit 1
[[ -f ch-3-conclusions/sec-3-2-outlook.md ]] || exit 1
[[ -f ch-3-conclusions/ch-3-compiled.md ]] || exit 1
[[ -f book.md ]] || exit 1

# Rename file and run again
mv ch-2-theory/sec-2-2-advanced.txt ch-2-theory/sec-2-2-original.txt
stepup watch-update ch-2-theory/sec-2-2-original.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ch-1-intro/sec-1-1-blabla.txt ]] || exit 1
[[ -f ch-1-intro/sec-1-2-some-more.txt ]] || exit 1
[[ -f ch-2-theory/sec-2-1-basics.txt ]] || exit 1
[[ ! -f ch-2-theory/sec-2-1-advanced.txt ]] || exit 1
[[ -f ch-2-theory/sec-2-2-original.txt ]] || exit 1
[[ -f ch-3-conclusions/sec-3-1-summary.txt ]] || exit 1
[[ -f ch-3-conclusions/sec-3-2-outlook.txt ]] || exit 1
[[ -f ch-1-intro/sec-1-1-blabla.md ]] || exit 1
[[ -f ch-1-intro/sec-1-2-some-more.md ]] || exit 1
[[ -f ch-1-intro/ch-1-compiled.md ]] || exit 1
[[ -f ch-2-theory/sec-2-1-basics.md ]] || exit 1
[[ ! -f ch-2-theory/sec-2-2-advanced.md ]] || exit 1
[[ -f ch-2-theory/sec-2-2-original.md ]] || exit 1
[[ -f ch-2-theory/ch-2-compiled.md ]] || exit 1
[[ -f ch-3-conclusions/sec-3-1-summary.md ]] || exit 1
[[ -f ch-3-conclusions/sec-3-2-outlook.md ]] || exit 1
[[ -f ch-3-conclusions/ch-3-compiled.md ]] || exit 1
[[ -f book.md ]] || exit 1

# Start stepup without checking expected output because watchdog file

# order is not reproducible.
rm .stepup/*.log
stepup boot -n 1 -w &

# Wait for watch phase.
stepup wait

# Test stepup clean with STEPUP_ROOT
(export STEPUP_ROOT="${PWD}"; cd ch-3-conclusions; stepup clean sec-3-1-summary.txt)
[[ -f ch-3-conclusions/sec-3-1-summary.txt ]] || exit 1
[[ ! -f ch-3-conclusions/sec-3-1-summary.md ]] || exit 1
[[ -f ch-3-conclusions/sec-3-2-outlook.md ]] || exit 1
[[ ! -f ch-3-conclusions/ch-3-compiled.md ]] || exit 1
[[ ! -f book.md ]] || exit 1

stepup join

# Wait for background processes, if any.
wait
