#!/usr/bin/env python3
from stepup.core.api import amend

path_foo = "../projects/public/foo.txt"
amend(out=path_foo)
with open(path_foo, "w") as fh:
    fh.write("Test\n")
