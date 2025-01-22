#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup -n 2 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
