#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo 2 > original.txt", out="original.txt")
copy("original.txt", "copy.txt")
