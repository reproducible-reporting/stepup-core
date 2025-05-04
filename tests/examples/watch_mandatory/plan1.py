#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo hello > foo.txt", out="foo.txt", optional=True)
