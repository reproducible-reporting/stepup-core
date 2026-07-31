#!/usr/bin/env python3
from stepup.core.api import glob

# Nothing declares data/a.txt static yet: the end-of-phase check reports a warning.
list(glob("data/*.txt"))
