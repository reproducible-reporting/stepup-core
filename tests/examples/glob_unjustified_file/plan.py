#!/usr/bin/env python3
from stepup.core.api import glob

# data/a.txt sits on disk but is never declared static and is not inside a static
# tree, so the end-of-phase check reports the match as a warning.
list(glob("data/*.txt"))
