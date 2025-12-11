#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo hello2 > ${out}", out="hello2.txt")
