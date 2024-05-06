#!/usr/bin/env python
import json

from stepup.core.api import step

# Load configuration
with open("config.json") as fh:
    config = json.load(fh)

# Write some output file
with open("output.txt", "w") as fh:
    print(config["msg"], file=fh)

# Use the log parameter to create a new step.
# Here just echo to keep things simple.
step(f"echo All is fine. > {config['log']}", out=[config["log"]])

# Add a step that writes something from the config to a fixed file.
step(f"echo log written to {config['log']}. > report.txt", out=["report.txt"])
