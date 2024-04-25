#!/usr/bin/env python
from stepup.core.api import step

step("echo hello > sub/dir/nested/hello.txt", out=["sub/dir/nested/hello.txt"])
