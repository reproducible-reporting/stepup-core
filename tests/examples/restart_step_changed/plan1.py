#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo 1 > original.txt", out="original.txt")
copy("original.txt", "copy.txt")
