#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo inp1 > foo1.txt", out="foo1.txt", optional=True)
runsh("echo inp2 > foo2.txt", out="foo2.txt", optional=True)
runsh("cat foo1.txt foo2.txt > bar.txt", inp=["foo1.txt", "foo2.txt"], out="bar.txt", optional=True)
runsh("cat bar.txt > egg.txt", inp="bar.txt", out="egg.txt", optional=True)
runsh("cat egg.txt > spam.txt", inp="egg.txt", out="spam.txt")
