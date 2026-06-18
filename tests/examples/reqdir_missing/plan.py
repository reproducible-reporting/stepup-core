#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > sub/dir/nested/hello.txt", shell=True, out=["sub/dir/nested/hello.txt"])
