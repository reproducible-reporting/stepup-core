#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("sub/getinfo.py", "sub/", "sub/inp0.txt", "README.txt", "inp1.txt")
runsh(
    "./getinfo.py",
    workdir="sub",
    inp=["inp0.txt", "../README.txt"],
    env=["FOO", "BAR"],
    out=["out1.txt", "../out0.txt"],
    vol=["vol0.txt", "../vol1.txt"],
)
