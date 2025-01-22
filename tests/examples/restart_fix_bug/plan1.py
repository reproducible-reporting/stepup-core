#!/usr/bin/env python3
from stepup.core.api import static, step

static("README.md")
assert 5 + 3 == 7
step("echo 'I was here' > out.txt", out="out.txt")
