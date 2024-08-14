#!/usr/bin/env python
from stepup.core.api import amend

path_foo = "../projects/public/foo.txt"
with open(path_foo, "w") as fh:
    fh.write("Test\n")
amend(out=path_foo)
