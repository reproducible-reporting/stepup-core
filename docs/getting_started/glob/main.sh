#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: src/intro.txt
# INP: src/method_notes.txt
# INP: src/result_notes.txt
