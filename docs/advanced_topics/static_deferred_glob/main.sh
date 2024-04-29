#!/usr/bin/env bash
git clean -qdfX .
unset STEPUP_ROOT
stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout.txt
dot -Nfontname="IBM Plex Sans" graph_creator.dot -Tsvg -o graph_creator.svg
dot -Nfontname="IBM Plex Sans" graph_supplier.dot -Tsvg -o graph_supplier.svg

# INP: plan.py
# INP: foo.txt
# INP: bar.txt
