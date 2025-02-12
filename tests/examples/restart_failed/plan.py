#!/usr/bin/env python3
from stepup.core.api import static, step

static("line.txt")
step("cat line.txt >> log.txt; exit 1", inp="line.txt", out="log.txt")
