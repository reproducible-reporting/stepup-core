#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo hello1 > ${out}", out="hello1.txt")
