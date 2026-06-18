#!/usr/bin/env python3
from stepup.core.api import copy, run

copy("foo.txt", "bar.txt")
run("echo test > foo.txt", shell=True, out="foo.txt")
