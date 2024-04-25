#!/usr/bin/env python
from stepup.core.api import static, copy, script

static("work/", "work/generate.py")
script("generate.py", "work/", optional=True)
copy("work/test.csv", "work/copy.csv")
