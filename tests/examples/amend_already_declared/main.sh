#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare the input.
echo "Hello" > inp.txt

# Run the plan. work.py amends the input, environment variable, output and volatile output
# that plan.py already declared for it, all of which are silently ignored.
export VAR="value"
sb -j 1 -w -e & # > current_stdout.txt &

# Get the graph, which must show none of the four as [dynamic].
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
grep Hello out.txt
grep value vol.txt
