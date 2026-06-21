#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
stepup boot --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout1.txt
mv dataset/bigfile.txt  dataset/tmp.txt
stepup boot --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout2.txt
mv  dataset/tmp.txt dataset/bigfile.txt

# INP: plan.py
# INP: expensive.py
