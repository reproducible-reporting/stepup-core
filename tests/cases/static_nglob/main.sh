#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Copy sec-2-2.txt in place
cp sec-2-2.txt ch-2-theory/sec-2-2-advanced.txt

# Run the example
stepup -w 1 plan.py & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f ch-1-intro/sec-1-1-blabla.md ]] || exit -1
[[ -f ch-1-intro/sec-1-2-some-more.md ]] || exit -1
[[ -f ch-1-intro/ch-1-compiled.md ]] || exit -1
[[ -f ch-2-theory/sec-2-1-basics.md ]] || exit -1
[[ -f ch-2-theory/sec-2-2-advanced.md ]] || exit -1
[[ -f ch-2-theory/ch-2-compiled.md ]] || exit -1
[[ -f ch-3-conclusions/sec-3-1-summary.md ]] || exit -1
[[ -f ch-3-conclusions/sec-3-2-outlook.md ]] || exit -1
[[ -f ch-3-conclusions/ch-3-compiled.md ]] || exit -1
[[ -f book.md ]] || exit -1

# Rename file and run again
mv ch-2-theory/sec-2-2-advanced.txt ch-2-theory/sec-2-2-original.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("ch-2-theory/sec-2-2-original.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f ch-1-intro/sec-1-1-blabla.txt ]] || exit -1
[[ -f ch-1-intro/sec-1-2-some-more.txt ]] || exit -1
[[ -f ch-2-theory/sec-2-1-basics.txt ]] || exit -1
[[ ! -f ch-2-theory/sec-2-1-advanced.txt ]] || exit -1
[[ -f ch-2-theory/sec-2-2-original.txt ]] || exit -1
[[ -f ch-3-conclusions/sec-3-1-summary.txt ]] || exit -1
[[ -f ch-3-conclusions/sec-3-2-outlook.txt ]] || exit -1
[[ -f ch-1-intro/sec-1-1-blabla.md ]] || exit -1
[[ -f ch-1-intro/sec-1-2-some-more.md ]] || exit -1
[[ -f ch-1-intro/ch-1-compiled.md ]] || exit -1
[[ -f ch-2-theory/sec-2-1-basics.md ]] || exit -1
[[ ! -f ch-2-theory/sec-2-2-advanced.md ]] || exit -1
[[ -f ch-2-theory/sec-2-2-original.md ]] || exit -1
[[ -f ch-2-theory/ch-2-compiled.md ]] || exit -1
[[ -f ch-3-conclusions/sec-3-1-summary.md ]] || exit -1
[[ -f ch-3-conclusions/sec-3-2-outlook.md ]] || exit -1
[[ -f ch-3-conclusions/ch-3-compiled.md ]] || exit -1
[[ -f book.md ]] || exit -1
