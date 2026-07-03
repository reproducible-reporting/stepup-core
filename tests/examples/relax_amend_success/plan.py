#!/usr/bin/env python3
from stepup.core.api import step

step("./producer.sh", out=["data.txt"])
step("./consumer.py")
