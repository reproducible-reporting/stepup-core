#!/usr/bin/env python3
from stepup.core.api import run

# Same optional producer, but its consumer is gone,
# so the producer reverts to OPTIONAL and both its outputs are cleaned up.
run(
    "echo out > out.txt; echo vol > vol.log",
    shell=True,
    out="out.txt",
    vol="vol.log",
    optional=True,
)
