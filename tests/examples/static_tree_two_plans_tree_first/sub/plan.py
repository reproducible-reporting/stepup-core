#!/usr/bin/env python3
from stepup.core.api import static

# A different step than the one that declared ../data/: the tree is already owned by
# ../plan.py, so this raises rather than being handed over.
static("../data/foo.txt")
