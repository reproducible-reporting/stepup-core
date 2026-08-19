#!/usr/bin/env python3
from stepup.core.api import run, static

# The same tree and file as in plan1.py, but declared in one call, which registers the
# tree before the file. This is the order plan1.py cannot produce: there, the file is
# declared while no tree exists yet, so it is the tree that takes the file over.
static("src/", "src/foo.txt")
run("cat src/foo.txt > copy.txt", shell=True, inp="src/foo.txt", out="copy.txt")
