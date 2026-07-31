#!/usr/bin/env python3
from stepup.core.api import static

# A different step than the one that declared ../data/foo.txt: the file is already owned
# by ../plan.py, so this raises rather than taking it over.
static("../data/")
