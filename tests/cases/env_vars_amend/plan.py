#!/usr/bin/env python
from stepup.core.api import static, step

static("demovars.py")
step("./demovars.py > output.txt", inp=["demovars.py"], out=["output.txt"])
