#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Prepare some variables
export ENV_VAR_TEST_STEPUP_AWDFTD="AAAA"
export ENV_VAR_TEST_STEPUP_DFTHYH="BBBB"

# Run the example
cp variables_01.json variables.json
stepup -e -w 1 plan.py & # > current_stdout_a.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep AAAA current_variables.txt
cp current_variables.txt current_variables_01.txt

# Rerun with changed file variables.json
cp variables_02.json variables.json
python3 - << EOD
from stepup.core.interact import *
watch_update("variables.json")
run()
wait()
graph("current_graph_02")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables_02.txt

# Rerun with UNchanged file variables.json
touch variables.json
python3 - << EOD
from stepup.core.interact import *
watch_update("variables.json")
run()
wait()
graph("current_graph_03")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables_03.txt

# Change a variable and restart
export ENV_VAR_TEST_STEPUP_DFTHYH="CCCC"
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_b.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_04")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep AAAA current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables_04.txt

# unset a variable and restart
unset ENV_VAR_TEST_STEPUP_AWDFTD
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_c.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_05")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep -v AAAA current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables_05.txt

# Wait for background processes, if any.
wait

# Set a variable again and restart
export ENV_VAR_TEST_STEPUP_AWDFTD="DDDD"
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_d.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_06")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep DDDD current_variables.txt
grep CCCC current_variables.txt
cp current_variables.txt current_variables_06.txt

# Wait for background processes, if any.
wait
