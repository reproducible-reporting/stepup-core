#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("cat first > second", inp="first", out="second")
runsh("cat second > first", inp="second", out="first")
