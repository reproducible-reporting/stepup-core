#!/usr/bin/env python3
from stepup.core.api import copy, mkdir, step

step("echo hello > hello.txt", out="hello.txt")
mkdir("sub/")
copy("hello.txt", "sub/")
