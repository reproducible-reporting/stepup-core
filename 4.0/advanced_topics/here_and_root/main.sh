#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
(cd source; sb -j 1) | sed -f ../../clean_stdout.sed > stdout.txt

# ROOT: source/
# INP: source/plan.py
# INP: source/sub/example.txt
# INP: source/sub/plan.py
