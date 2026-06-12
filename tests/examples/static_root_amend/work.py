#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="dir_inp/inp.txt", out="dir_out/out.txt", vol="dir_vol/vol.txt")
with open("dir_inp/inp.txt") as fh:
    text = fh.read().strip()
with open("dir_out/out.txt", "w") as fh:
    print(f"Amended out {fh.name}", file=fh)
    print(text, file=fh)
with open("dir_vol/vol.txt", "w") as fh:
    print(f"Amended vol {fh.name}", file=fh)
    print(text, file=fh)
