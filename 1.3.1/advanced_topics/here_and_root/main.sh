#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
(cd source; stepup -n -w1) | sed -f ../../clean_stdout.sed > stdout.txt

# ROOT: source/
# INP: source/plan.py
# INP: source/sub/
# INP: source/sub/example.txt
# INP: source/sub/plan.py
