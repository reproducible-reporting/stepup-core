#!/usr/bin/env python3
from stepup.core.api import run

run("echo inp1 > foo1.txt", shell=True, out="foo1.txt", optional=True)
run("echo inp2 > foo2.txt", shell=True, out="foo2.txt", optional=True)
run(
    "cat foo1.txt foo2.txt > bar.txt",
    shell=True,
    inp=["foo1.txt", "foo2.txt"],
    out="bar.txt",
    optional=True,
)
run("cat bar.txt > egg.txt", shell=True, inp="bar.txt", out="egg.txt", optional=True)
