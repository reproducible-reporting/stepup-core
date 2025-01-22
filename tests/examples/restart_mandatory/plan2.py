#!/usr/bin/env python3
from stepup.core.api import copy, step

step("echo hello > foo.txt", out="foo.txt", optional=True)
copy("foo.txt", "bar.txt")
