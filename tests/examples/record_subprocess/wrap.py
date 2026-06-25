#!/usr/bin/env python3
from stepup.core.extapi import run_subprocess

# Run a real subprocess and record it for archival. The subprocess writes the step output
# and receives an environment overlay (GREETING) on top of the inherited environment.
# Only the overlay is recorded, not the full resolved environment.
command = "GREETING=hello awk 'BEGIN { print ENVIRON[\"GREETING\"] }'"
run_subprocess(command)
