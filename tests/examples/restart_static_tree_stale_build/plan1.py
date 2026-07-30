#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > data/foo.txt", shell=True, out="data/foo.txt")
copy("data/foo.txt", "result.txt")
