#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("bunch.py")
info = runpy(
    "./bunch.py", inp="bunch.py", out=["sub/", "sub/other/", "sub/other/foo/", "sub/other/foo/text"]
)

# Tests for info object, only useful for testing
if info.filter_out("sub/*").single() != "sub/other/":
    raise AssertionError("Wrong info.filter_out")
if info.filter_out("sub/other/**").files() != (
    "sub/other/",
    "sub/other/foo/",
    "sub/other/foo/text",
):
    raise AssertionError("Wrong info.filter_out")
