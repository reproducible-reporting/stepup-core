#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup boot --no-progress -n 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: part1.txt
# INP: sub/
# INP: sub/part2.txt
