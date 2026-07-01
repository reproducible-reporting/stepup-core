#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > hello.txt", shell=True, out="hello.txt")
copy("hello.txt", "sub/")
