#!/usr/bin/env python3
from stepup.core.api import run, static

static("src/foo.txt")
run("cat src/foo.txt > copy.txt", shell=True, inp="src/foo.txt", out="copy.txt")
# Same creator as the file above, so this hands src/foo.txt over to the tree instead of
# raising.
static("src/")
