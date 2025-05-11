#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup boot --no-progress -n 2 | sed -f ../../clean_stdout.sed > stdout.txt
dot -Nfontname="IBM Plex Sans" graph_provenance.dot -Tsvg -o graph_provenance.svg
dot -Nfontname="IBM Plex Sans" graph_dependency.dot -Tsvg -o graph_dependency.svg

# INP: plan.py
# OUT: graph_provenance.dot
# OUT: graph_dependency.dot
# OUT: graph_provenance.svg
# OUT: graph_dependency.svg
