#!/usr/bin/env python3
import contextlib

from stepup.core.api import amend, track_amend
from stepup.core.stepinfo import dump_step_info

amend(inp="../b.txt")
with track_amend() as tracker:
    # Recorded, although it was amended before the block.
    amend(inp="../b.txt")
    with contextlib.chdir("data"):
        # Recorded relative to the directory at the start of the block.
        amend(inp="a.txt", out="a.out")
        with open("a.out", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("a\n")
    with track_amend() as inner:
        # Recorded by both trackers.
        amend(env="FOO", vol="vol.txt")
        with open("vol.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("vol\n")
# Not recorded, because the block is closed.
amend(env="BAR")
# An input added to the tracker without amending it.
tracker.inp.add("./work.py")
assert inner.get_info().vol == ["vol.txt"]
dump_step_info("../current_tracked_info.json", tracker.get_info())
