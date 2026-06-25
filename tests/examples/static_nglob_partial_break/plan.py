#!/usr/bin/env python3
from stepup.core.api import glob, run

for match in glob("head_${*char}.txt", "tail_${*char}.txt", char="?"):
    ch = match.char
    run(
        f"paste -d ' ' head_{ch}.txt tail_{ch}.txt > paste_{ch}.txt",
        shell=True,
        inp=match.files,
        out=[f"paste_{ch}.txt"],
    )
