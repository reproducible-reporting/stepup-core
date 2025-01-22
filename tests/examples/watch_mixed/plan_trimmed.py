#!/usr/bin/env python3
from stepup.core.api import step

step("cp copy1.txt copy2.txt", inp=["copy1.txt"], out=["copy2.txt"])
