#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: matplotlibrc
# INP: generate.py
# INP: plot.py
