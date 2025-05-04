#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo hello > ${out}", out="hello.txt")
