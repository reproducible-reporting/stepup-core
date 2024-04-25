#!/usr/bin/env python
from stepup.core.api import step, copy

copy("foo.txt", "bar.txt")
step("echo test > foo.txt", out="foo.txt")
