#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with label=v1.
stepup boot -j 1 -w & # > current_stdout1.txt &
stepup wait
stepup graph current_graph1
grep "v1: hello" result.txt

# Change config to label=v2, which causes plan to rewrite args.json.
echo '{"label": "v2"}' > config.json
stepup watch-update config.json
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Verify that the run step re-executed with the new args_file content.
grep "v2: hello" result.txt
