#!/usr/bin/env bash

ASCIINEMA=${PWD}
TEMPLATE=${PWD}/../../nobackup/plain-pip/

# Go to right location and prepare
cd $TEMPLATE
source .envrc
cd latest-draft
rm -rf .stepup
sed -e 's/x + shift/x - shift/' -i results-example/generate.py

# Change generate.py with 13 second delay
(sleep 13; sed -e 's/x - shift/x + shift/' -i results-example/generate.py) &

# Start recording
autocast ${ASCIINEMA}/template-autocast.yaml ${ASCIINEMA}/template-orig.cast --overwrite

# Merge with markers
cd ${ASCIINEMA}
./insert_markers.py template-orig.cast template-markers.cast template.cast

# Remove trailing prompt
sed -e :a -e '$d;N;2,3ba' -e 'P;D' -i template.cast
