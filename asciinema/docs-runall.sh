#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later

ASCIINEMA=${PWD}
DOCS=${PWD}/../docs/

# Go to right location and prepare
cd $DOCS
rm -rf .stepup

# Start recording
export PS1='$ '
autocast ${ASCIINEMA}/docs-autocast.yaml ${ASCIINEMA}/docs.cast --overwrite

cd ${ASCIINEMA}

# Remove trailing prompt
sed -e :a -e '$d;N;2,3ba' -e 'P;D' -i docs.cast
