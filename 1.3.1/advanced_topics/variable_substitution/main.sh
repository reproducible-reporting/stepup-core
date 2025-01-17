#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
MYVAR=foo stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: step.py
# INP: src_foo.txt
