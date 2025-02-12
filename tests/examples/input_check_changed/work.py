#!/usr/bin/env python3

# Write f2.txt, so its absence is not the cause of the error.
with open("f2.txt", "w") as fh:
    print("bye", file=fh)

# This is an evil example. Don't do this in production code.
with open("f1.txt", "w") as fh:
    print("this is not ok", file=fh)
