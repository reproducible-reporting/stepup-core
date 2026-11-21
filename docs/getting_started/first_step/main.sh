#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout1.txt
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout2.txt

# Unset the variables that a director gives to its steps, so ./plan.py runs on its own.
unset STEPUP_DIRECTOR_SOCKET
unset STEPUP_JOB_I
STEPUP_DEBUG=1 ./plan.py > stdout3.txt 2>&1

# INP: plan.py
# OUT: stdout3.txt
