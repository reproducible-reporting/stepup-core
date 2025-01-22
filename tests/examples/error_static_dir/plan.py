#!/usr/bin/env python3
from stepup.core.api import static, step

static("subdir")
step("cat subdir", inp="subdir")
