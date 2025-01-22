#!/usr/bin/env python3
from stepup.core.api import static, step

static("sub/getinfo.py", "sub/", "sub/inp0.txt", "README.md", "inp1.txt")
step(
    "./getinfo.py",
    workdir="sub",
    inp=["inp0.txt", "../README.md"],
    env=["FOO", "BAR"],
    out=["out1.txt", "../out0.txt"],
    vol=["vol0.txt", "../vol1.txt"],
)
