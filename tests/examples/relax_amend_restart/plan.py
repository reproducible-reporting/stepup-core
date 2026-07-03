#!/usr/bin/env python3
from stepup.core.api import static, step

static("trigger.txt")
step("./producer.sh", out=["data.txt"])
step("./consumer.py", inp=["trigger.txt"])
