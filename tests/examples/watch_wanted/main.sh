#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
touch static.txt
touch input1.txt
touch input2.txt
cp plan1.py plan.py
stepup -w -n 1 & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1

# Remove plan and rerun.
rm plan.py
python3 - << EOD
from stepup.core.interact import *
watch_delete("plan.py")
run()
wait()
graph("current_graph2")
EOD

# Check files that are expected to be present and/or missing.
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1

# Replace plan and rerun.
cp plan2.py plan.py
python3 - << EOD
from stepup.core.interact import *
watch_update("plan.py")
run()
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static.txt ]] || exit 1
[[ -f input1.txt ]] || exit 1
[[ -f input2.txt ]] || exit 1
[[ ! -f output1.txt ]] || exit 1
[[ ! -f output2.txt ]] || exit 1
