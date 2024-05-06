#!/usr/bin/env python
from stepup.core.api import copy, static, step

static("config.json")
static("use_config.py")
step("./use_config.py", inp=["use_config.py", "config.json"], out=["output.txt"])
copy("report.txt", "copy.txt")
