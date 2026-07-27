#!/usr/bin/env python3
"""Write the label given as $1 to the output file given as $2."""

import sys
from pathlib import Path

label, out_path = sys.argv[1:]
Path(out_path).write_text(f"{label}\n")
