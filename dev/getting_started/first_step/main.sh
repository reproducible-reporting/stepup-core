#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout1.txt
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout2.txt

# Some more variables need to be unset to get output from ./plan.py
unset STEPUP_DIRECTOR_SOCKET
unset STEPUP_STEP_I
./plan.py > stdout3.txt

# INP: plan.py
# OUT: stdout3.txt
