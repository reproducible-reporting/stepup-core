#!/usr/bin/env python3
from stepup.core.api import static, step

static("big.py", "fail.py")
# Two independent steps: one emits more output than the configured limit, the other
# fails after writing output. Both store their output in the step table's stdout/stderr columns.
step("./big.py", inp=["big.py"])
step("./fail.py", inp=["fail.py"])
