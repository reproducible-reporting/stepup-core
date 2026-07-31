#!/usr/bin/env python3
from stepup.core.api import run, static

# Within a single static() call, directory arguments are always registered
# before file arguments, regardless of the order given here. So the tree
# already owns src/foo.txt by the time the file is processed. The file
# declaration is a no-op for the build result: the tree is its owner either
# way, and that would also hold if the file had been declared first. It is
# not a no-op for the graph, though: src/foo.txt gets a node right away,
# rather than only once the run() step below first uses it as an input.
static("src/", "src/foo.txt")
run("cat src/foo.txt > copy.txt", shell=True, inp="src/foo.txt", out="copy.txt")
