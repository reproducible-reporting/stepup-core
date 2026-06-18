#!/usr/bin/env python3
from stepup.core.api import run, static

static("README.txt")
assert 5 + 3 == 7
run("echo 'I was here' > out.txt", shell=True, out="out.txt")
