#!/usr/bin/env python3
from stepup.core.api import step

step("echo hello > sub/dir/nested/hello.txt", out=["sub/dir/nested/hello.txt"])
