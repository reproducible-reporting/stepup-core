#!/usr/bin/env python3
# ruff: noqa: EXE001
from stepup.core.api import runsh

runsh("rm .sjdksjdfkjasdfkdjsak", out=["oops.txt"])
