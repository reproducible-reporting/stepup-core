#!/usr/bin/env python3
from stepup.core.api import copy, static

static("hop1.txt")
copy("hop1.txt", "hop2.txt", optional=True)
copy("hop2.txt", "hop3.txt")
