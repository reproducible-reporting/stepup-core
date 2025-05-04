#!/usr/bin/env python3
from stepup.core.api import copy, runsh

copy("foo.txt", "bar.txt")
runsh("echo test > foo.txt", out="foo.txt")
