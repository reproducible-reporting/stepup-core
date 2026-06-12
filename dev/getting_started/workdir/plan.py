#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo 'a friendly file' > ${out}", workdir="out/", out="hello.txt")
