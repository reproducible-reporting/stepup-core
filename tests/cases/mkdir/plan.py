#!/usr/bin/env python
from stepup.core.api import mkdir, step

mkdir("sub")
step("touch sub/foo.txt", inp=["sub/"], out=["sub/foo.txt"])
# Creating an existing directory is ok, as long it will never contain static paths.
mkdir("exists")
