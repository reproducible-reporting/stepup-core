#!/usr/bin/env python3
from stepup.core.api import step

step("./source.sh", out=["data.txt"])
step("./sink.py")
