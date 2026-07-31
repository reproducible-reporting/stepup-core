#!/usr/bin/env python3
from stepup.core.api import run, static

# Declare the file first, use it as a step input, and only then declare the tree that
# contains it. The consumer in between is the point: it makes the hand-over observable,
# since a file with no consumers would look the same whether it started out owned by
# plan.py or by the tree.
static("src/foo.txt")
run("cat src/foo.txt > copy.txt", shell=True, inp="src/foo.txt", out="copy.txt")
# Same creator as the file above, so this is a no-op for the build: src/foo.txt is
# handed over to the tree instead of raising.
static("src/")
