#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
stepup -n -w 2 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
