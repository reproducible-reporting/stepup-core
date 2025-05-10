#!/usr/bin/env bash

ASCIINEMA=${PWD}
DOCS=${PWD}/../docs/

# Go to right location and prepare
cd $DOCS
rm -rf .stepup

# Start recording
autocast ${ASCIINEMA}/docs-autocast.yaml ${ASCIINEMA}/docs.cast --overwrite

cd ${ASCIINEMA}

# Remove trailing prompt
sed -e :a -e '$d;N;2,3ba' -e 'P;D' -i docs.cast
