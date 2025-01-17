#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout1.txt
mv dataset tmp
stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout2.txt
mv tmp dataset

# INP: plan.py
# INP: expensive.py
