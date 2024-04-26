#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: plot.py
# INP: ebbr.csv
# INP: ebos.csv
# INP: matplotlibrc
