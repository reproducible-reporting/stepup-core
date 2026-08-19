#!/usr/bin/env python3
from stepup.core.api import run

run("echo keep > keep.txt", shell=True, out="keep.txt")
# The output is written outside the working directory, so sub/ is only needed as workdir.
run("echo hello > ../hello.txt", shell=True, workdir="sub", out="../hello.txt")
