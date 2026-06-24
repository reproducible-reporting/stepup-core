#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
export SOURCE_DATE_EPOCH="315532800"
export PUBLIC="public/"
sb -w -j 1 & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f variables.py ]] || exit 1
[[ -f static/main.tex ]] || exit 1
[[ -f static/plan.py ]] || exit 1
[[ -f static/preamble.inc.tex ]] || exit 1
[[ -f static/variables.py ]] || exit 1
[[ -f public/preamble.inc.tex ]] || exit 1
[[ -f public/main.tex ]] || exit 1
grep '\\input{./preamble.inc.tex}' public/main.tex
grep Everything public/main.tex
