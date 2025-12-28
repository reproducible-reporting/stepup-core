#!/usr/bin/env python3
from stepup.core.api import call, static

static("wavegen.py")
call("wavegen.py", out="wave1.json", freq=440.0, duration=1.0)
call("wavegen.py", out="wave2.json", freq=380.0, duration=0.5, rate=22050)
