#!/usr/bin/env bash
git clean -dfX .
unset STEPUP_ROOT
stepup -n -w4 | sed -f ../../clean_stdout.sed > stdout.txt
