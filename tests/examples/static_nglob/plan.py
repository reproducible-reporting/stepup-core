#!/usr/bin/env python3
from path import Path

from stepup.core.api import copy, glob, run, shq

# Conversion of source files into markdown files
chapters = {}
for m_sec in glob("ch-*/sec-${*ch}-${*sec}-${*name}.txt", ch="[0-9]"):
    # Mimic compilation of a section with copy from txt to md
    print(f"Planning sec {m_sec.sec} {m_sec.name}")
    path_sec = Path(m_sec.single[:-3] + "md")
    copy(m_sec.single, path_sec)
    chapters.setdefault(path_sec.parent, []).append(path_sec)

paths_ch = []
for dir_ch, paths_sec in chapters.items():
    # Mimic concatenation of sections with cat
    path_ch = dir_ch / "compiled.md"
    run(f"cat {shq(paths_sec)} > {shq([path_ch])}", shell=True, inp=paths_sec, out=[path_ch])
    paths_ch.append(path_ch)

# Mimic concatenation of chapters with cat
run(f"cat {shq(paths_ch)} > book.md", shell=True, inp=paths_ch, out=["book.md"])
