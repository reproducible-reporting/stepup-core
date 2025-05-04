#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo hello > sub/dir/nested/hello.txt", out=["sub/dir/nested/hello.txt"])
