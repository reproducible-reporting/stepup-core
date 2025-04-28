#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup --no-progress -n 1 | sed -f ../../clean_stdout.sed > stdout1.txt
mv dataset tmp
stepup --no-progress -n 1 | sed -f ../../clean_stdout.sed > stdout2.txt
mv tmp dataset

# INP: plan.py
# INP: expensive.py
