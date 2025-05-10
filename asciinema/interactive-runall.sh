#!/usr/bin/env bash

ASCIINEMA=${PWD}
TUTORIAL=${PWD}/../docs/getting_started/static_glob/

# Go to right location and prepare
cd $TUTORIAL
rm -rf .stepup
git restore src/

# Change src/foo.txt with 6 second delay
(sleep 6; echo 'The contents of foo have changed.' > src/foo.txt) &

# Add src/spam.txt with 20 second delay
(sleep 20; echo 'This is spam!' > src/spam.txt) &

# Start recording
autocast ${ASCIINEMA}/interactive-autocast.yaml ${ASCIINEMA}/interactive-orig.cast --overwrite

# Undo changes
git restore src/
rm src/spam.txt

# Merge with markers
cd ${ASCIINEMA}
./insert_markers.py interactive-orig.cast interactive-markers.cast interactive.cast

# Remove trailing prompt
sed -e :a -e '$d;N;2,3ba' -e 'P;D' -i interactive.cast
