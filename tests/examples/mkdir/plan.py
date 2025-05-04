#!/usr/bin/env python3
from stepup.core.api import mkdir, runsh

mkdir("sub")
runsh("echo a > sub/foo.txt", inp=["sub/"], out=["sub/foo.txt"])
# Creating an existing directory is ok, as long it will never contain static paths.
mkdir("exists")
