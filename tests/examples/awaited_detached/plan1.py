#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("cat src1.txt", inp=["src1.txt"])
runsh("cat src2.txt", inp=["src2.txt"])
