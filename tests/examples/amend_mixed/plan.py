#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py")
run("./work.py > log.txt", shell=True, inp="work.py", out="log.txt")
static("inp0.txt", "inp1.txt")
run("cp inp1.txt tmp1.txt", inp="inp1.txt", out="tmp1.txt")
run("echo Contents of inp2.txt > inp2.txt", shell=True, out="inp2.txt")
