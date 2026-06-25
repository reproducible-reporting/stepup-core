#!/usr/bin/env python3
from stepup.core.api import static, step

static("wrap.py")
# A wrapper step that internally runs a subprocess and records the exact invocation
# in the step_subprocess table via run_subprocess.
step("./wrap.py", inp=["wrap.py"])
