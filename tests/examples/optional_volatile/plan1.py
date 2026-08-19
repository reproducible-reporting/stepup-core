#!/usr/bin/env python3
from stepup.core.api import run

# Optional producer with both a regular and a volatile output.
run(
    "echo out > out.txt; echo vol > vol.log",
    shell=True,
    out="out.txt",
    vol="vol.log",
    optional=True,
)
# Consumer of the regular output, which makes the producer needed.
run("cat out.txt", shell=True, inp="out.txt")
