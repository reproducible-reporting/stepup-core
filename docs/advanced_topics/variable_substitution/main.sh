#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
MYVAR=foo stepup boot -n 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: step.py
# INP: src_foo.txt
