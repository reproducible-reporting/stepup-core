#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare some variables
export ENV_VAR_TEST_STEPUP_AWDFTD="AAAA"
export ENV_VAR_TEST_STEPUP_DFTHYH="BBBB"

# Run the example
cp variables1.json variables.json
stepup -w -e -n 1 plan.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
cp current_variables.txt current_variables1.txt

# Rerun with changed file variables.json
cp variables2.json variables.json
python3 - << EOD
from stepup.core.interact import *
watch_update("variables.json")
run()
wait()
graph("current_graph2")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables2.txt

# Rerun with UNchanged file variables.json
touch variables.json
python3 - << EOD
from stepup.core.interact import *
watch_update("variables.json")
run()
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables3.txt

# Change a variable and restart
export ENV_VAR_TEST_STEPUP_DFTHYH="CCCC"
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph4")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables4.txt

# unset a variable and restart
unset ENV_VAR_TEST_STEPUP_AWDFTD
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout3.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph5")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep -v AAAA current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables5.txt

# Wait for background processes, if any.
wait

# Set a variable again and restart
export ENV_VAR_TEST_STEPUP_AWDFTD="DDDD"
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout4.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph6")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep DDDD current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables_06.txt

# Wait for background processes, if any.
wait
