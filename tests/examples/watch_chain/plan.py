#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("config.json")
static("use_config.py")
info = runsh("./use_config.py", inp=["use_config.py", "config.json"], out=["output.txt"])
copy("report.txt", "copy.txt")

# Test info object, only useful for testing
if info.filter_inp("*.py").single() != "use_config.py":
    raise AssertionError("Wrong info.filter_inp")
if info.filter_inp("config.*").single() != "config.json":
    raise AssertionError("Wrong info.filter_inp")
