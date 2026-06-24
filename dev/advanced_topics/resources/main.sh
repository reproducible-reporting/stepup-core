#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
export STEPUP_BUILD_RESOURCES="cpu:2,gpu:1"
stepup build --no-progress -j 4 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
