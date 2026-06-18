#!/usr/bin/env python3
import json

from stepup.core.api import run

# Load configuration
with open("config.json") as fh:
    config = json.load(fh)

# Write some output file
with open("output.txt", "w") as fh:
    print(config["msg"], file=fh)

# Use the log parameter to create a new runsh.
# Here just echo to keep things simple.
run(f"echo All is fine. > {config['log']}", shell=True, out=[config["log"]])

# Add a runsh that writes something from the config to a fixed file.
run(f"echo log written to {config['log']}. > report.txt", shell=True, out=["report.txt"])
