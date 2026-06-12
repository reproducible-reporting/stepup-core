#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo hello > ${out}", out="hello.txt")
copy("hello.txt", "other/")
