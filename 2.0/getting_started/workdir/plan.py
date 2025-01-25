#!/usr/bin/env python3
from stepup.core.api import static, step

static("out/")
step("echo 'a friendly file' > ${out}", workdir="out/", out="hello.txt")
