#!/usr/bin/env python3
from stepup.core.api import static, step

static("trigger.txt")
step("./source.sh", out=["data.txt"])
step("./sink.py", inp=["trigger.txt"])
