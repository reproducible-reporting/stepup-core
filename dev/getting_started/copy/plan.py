#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo hello > hello.txt", out="hello.txt")
copy("hello.txt", "sub/")
