#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
stepup build --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: plot.py
# INP: ebbr.csv
# INP: ebos.csv
# INP: matplotlibrc
