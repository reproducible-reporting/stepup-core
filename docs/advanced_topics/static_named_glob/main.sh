#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout.txt

# INP: plan.py
# INP: ch1/
# INP: ch1/sec1_1_introduction.txt
# INP: ch1/sec1_2_objectives.txt
# INP: ch2/
# INP: ch2/sec2_1_mathematical_requisites.txt
# INP: ch2/sec2_2_theory.txt
# INP: ch3/
# INP: ch3/sec3_1_applications.txt
# INP: ch3/sec3_2_discussion.txt
# INP: ch4/
# INP: ch4/sec4_1_summary.txt
