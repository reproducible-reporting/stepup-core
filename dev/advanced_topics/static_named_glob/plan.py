#!/usr/bin/env python3
from stepup.core.api import copy, glob, run, shq

# Enforce consistent chapter numbers throughout the match,
# ignoring inconsistent txt files.
md_chapter = {}
for match in glob("ch${*ch}/sec${*ch}_${*sec}_${*name}.txt", ch="[0-9]", sec="[0-9]"):
    path_txt = match.single
    path_md = path_txt[:-3] + "md"
    copy(path_txt, path_md)
    md_chapter.setdefault(match.ch, []).append(path_md)

# Concatenate all markdown files per chapter
for ch, paths_md in md_chapter.items():
    path_out = f"public/ch{ch}.md"
    run(f"cat {shq(paths_md)} > {shq(path_out)}", shell=True, inp=paths_md, out=path_out)
