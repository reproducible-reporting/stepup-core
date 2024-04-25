#!/usr/bin/env python
from stepup.core.api import static, step

static("subdir")
step("cat subdir", inp="subdir")
