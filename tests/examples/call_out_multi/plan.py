#!/usr/bin/env python3
from stepup.core.api import call, static

static("transform.py", "inp1.txt", "inp2.txt")
call(
    "transform.py",
    repeat=5,
    inp=[None, "inp1.txt", "inp2.txt"],
    out=[False, "out1.txt", "out2.txt"],
)
