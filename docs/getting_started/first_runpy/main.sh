#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
stepup build --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: work.py
# INP: helper.py
# OUT: stdout.txt
