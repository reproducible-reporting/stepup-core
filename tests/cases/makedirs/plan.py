#!/usr/bin/env python
from stepup.core.api import static, step

static("bunch.py")
info = step("./bunch.py", out=["sub/", "sub/other/", "sub/other/foo/", "sub/other/foo/text"])

# Tests for info object, only useful for testing
if list(info.filter_out("sub/*")) != ["sub/other/"]:
    raise AssertionError("Wrong info.filter_out")
if list(info.filter_out("sub/other/**")) != ["sub/other/", "sub/other/foo/", "sub/other/foo/text"]:
    raise AssertionError("Wrong info.filter_out")
