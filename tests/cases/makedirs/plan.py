#!/usr/bin/env python
from stepup.core.api import static, step

static("bunch.py")
step("./bunch.py", out=["sub/", "sub/other/", "sub/other/foo/", "sub/other/foo/text"])
