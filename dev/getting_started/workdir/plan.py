#!/usr/bin/env python3
from stepup.core.api import run

run("echo 'a friendly file' > ${out}", shell=True, workdir="out/", out="hello.txt")
