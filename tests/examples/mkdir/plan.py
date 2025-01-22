#!/usr/bin/env python3
from stepup.core.api import mkdir, step

mkdir("sub")
step("echo a > sub/foo.txt", inp=["sub/"], out=["sub/foo.txt"])
# Creating an existing directory is ok, as long it will never contain static paths.
mkdir("exists")
