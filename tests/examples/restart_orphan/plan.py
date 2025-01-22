#!/usr/bin/env python3
from stepup.core.api import copy, step

copy("foo.txt", "bar.txt")
step("echo test > foo.txt", out="foo.txt")
