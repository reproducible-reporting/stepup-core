#!/usr/bin/env python3
"""Write the label given as an argument to the output file, after an optional delay."""

import sys
import time
from pathlib import Path

args = sys.argv[1:]
sleep_seconds = 0.0
if args and args[0].startswith("--sleep="):
    sleep_seconds = float(args[0].split("=", 1)[1])
    args = args[1:]
label, out_path = args
time.sleep(sleep_seconds)
Path(out_path).write_text(f"{label}\n")
