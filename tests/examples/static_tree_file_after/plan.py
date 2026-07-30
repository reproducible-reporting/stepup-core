#!/usr/bin/env python3
from stepup.core.api import run, static

# Within a single static() call, directory arguments are always registered
# before file arguments, regardless of the order given here. So the tree
# already owns src/foo.txt by the time the file is processed, and declaring
# the file here is a no-op: no separate node is created for src/foo.txt.
static("src/", "src/foo.txt")
run("cat src/foo.txt > copy.txt", shell=True, inp="src/foo.txt", out="copy.txt")
