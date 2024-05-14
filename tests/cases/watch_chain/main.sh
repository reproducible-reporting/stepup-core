#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp config_01.json config.json
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f script.log ]] || exit -1
grep script.log report.txt
grep script.log copy.txt
[[ -f output.txt ]] || exit -1

# Modify config.json and rerun.
cp config_02.json config.json
python3 - << EOD
from stepup.core.interact import *
watch_update("config.json")
run()
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f script.log ]] || exit -1
[[ -f other.log ]] || exit -1
grep other.log report.txt
grep other.log copy.txt
[[ -f output.txt ]] || exit -1
