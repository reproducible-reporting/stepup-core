#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > foo.txt", shell=True, out="foo.txt", optional=True)
copy("foo.txt", "bar.txt")
