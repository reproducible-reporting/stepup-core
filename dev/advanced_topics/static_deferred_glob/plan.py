#!/usr/bin/env python3
from stepup.core.api import glob, graph, runsh

glob("*.txt", _defer=True)
runsh("cat foo.txt", inp="foo.txt")
graph("graph")
