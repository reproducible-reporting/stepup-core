#!/usr/bin/env python3
from stepup.core.api import glob, run

# inp.txt is globbed but never declared static, so it stays an AWAITED input with no
# producer: the loud failure mode of the glob()-to-static() migration.
for path in glob("inp.txt"):
    run("cat inp.txt > out.txt", shell=True, inp=[path], out="out.txt")
