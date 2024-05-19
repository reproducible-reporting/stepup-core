#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
xargs rm -rvf < .gitignore

# Run the example
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get graph after normal run.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ -f inp1.txt ]] || exit -1
[[ -f inp2.txt ]] || exit -1
grep word1 out1.txt
grep word2 out2.txt

# Change an amended input, rerun, and get the graph after completion of the pending steps.
echo "word2 and some" > inp2.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp2.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ -f inp1.txt ]] || exit -1
[[ -f inp2.txt ]] || exit -1
grep word1 out1.txt
grep word2 out2.txt

# Wait for background processes, if any.
wait

# Restart StepUp without changes
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get graph after restart without changes.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_03")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ -f inp1.txt ]] || exit -1
[[ -f inp2.txt ]] || exit -1
grep word1 out1.txt
grep word2 out2.txt

# Wait for background processes, if any.
wait

# Restart StepUp with changes
echo "word2 and other" > inp2.txt
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_03.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get graph after restart without changes.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_04")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ -f inp1.txt ]] || exit -1
[[ -f inp2.txt ]] || exit -1
grep word1 out1.txt
grep word2 out2.txt

# Wait for background processes, if any.
wait
