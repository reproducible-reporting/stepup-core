#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup boot --no-progress -n 1 | sed -f ../../clean_stdout.sed > stdout.txt
./plot.py cases > cases_out.txt

# INP: plan.py
# INP: plot.py
# INP: ebbr.csv
# INP: ebos.csv
# INP: matplotlibrc
