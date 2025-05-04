#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("README.txt")
assert 5 + 3 == 7
runsh("echo 'I was here' > out.txt", out="out.txt")
