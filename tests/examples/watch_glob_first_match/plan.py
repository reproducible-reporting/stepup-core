#!/usr/bin/env python3
from stepup.core.api import glob

print("MATCHES:", sorted(str(p) for p in glob("data/*.txt")))
