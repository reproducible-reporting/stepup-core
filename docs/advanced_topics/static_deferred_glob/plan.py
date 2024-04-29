#!/usr/bin/env python
from stepup.core.api import glob, step
from stepup.core.interact import graph

glob("*.txt", _defer=True)
step("cat foo.txt", inp="foo.txt")
graph("graph")
