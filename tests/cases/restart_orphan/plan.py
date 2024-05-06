#!/usr/bin/env python
from stepup.core.api import copy, step

copy("foo.txt", "bar.txt")
step("echo test > foo.txt", out="foo.txt")
