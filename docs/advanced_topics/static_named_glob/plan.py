#!/usr/bin/env python
from stepup.core.api import copy, glob, mkdir, step

# Make all chapter directories static
glob("ch*/")

# Enforce consistent chapter numbers throughout the match,
# ignoring inconsistent txt files.
md_chapter = {}
for match in glob("ch${*ch}/sec${*ch}_${*sec}_${*name}.txt", ch="[0-9]", sec="[0-9]"):
    path_txt = match.files[0]
    path_md = path_txt[:-3] + "md"
    copy(path_txt, path_md)
    md_chapter.setdefault(match.ch, []).append(path_md)

# Concatenate all markdown files per chapter
mkdir("public/")
for ch, paths_md in md_chapter.items():
    step("cat ${inp} > ${out}", inp=paths_md, out=f"public/ch{ch}.md")
