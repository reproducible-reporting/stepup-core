#!/usr/bin/env python3
from stepup.core.api import static, step

static("limerick.txt")
step("nl ${inp} > ${out}", inp="limerick.txt", out="numbered.txt")
