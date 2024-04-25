#!/usr/bin/env python
from stepup.core.api import glob, step

for match in glob("head_${*char}.txt", "tail_${*char}.txt", char="?"):
    ch = match.char
    step(
        f"paste -d ' ' head_{ch}.txt tail_{ch}.txt > paste_{ch}.txt",
        inp=match.files,
        out=[f"paste_{ch}.txt"],
    )
