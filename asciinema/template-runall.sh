#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later

ASCIINEMA=${PWD}
TEMPLATE=${PWD}/../../nobackup/plain-uv/

# Go to right location and prepare
cd $TEMPLATE
source .envrc
export PS1='$ '  # Needed for autocast to detect the prompt correctly
cd latest-draft
rm -rf .stepup
sed -e 's/x + shift/x - shift/' -i results-example/generate.py

# Change generate.py with 10 second delay
(sleep 10; sed -e 's/x - shift/x + shift/' -i results-example/generate.py) &

# Start recording
autocast ${ASCIINEMA}/template-autocast.yaml ${ASCIINEMA}/template-orig.cast --overwrite

# Merge with markers
cd ${ASCIINEMA}
./insert_markers.py template-orig.cast template-markers.cast template.cast

# Remove trailing prompt
sed -e :a -e '$d;N;2,3ba' -e 'P;D' -i template.cast
