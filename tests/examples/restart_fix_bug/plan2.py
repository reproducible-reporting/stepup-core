#!/usr/bin/env python3
from stepup.core.api import static, step

static("README.txt")
assert 5 + 3 == 8
step("echo 'I was here' > out.txt", out="out.txt")
