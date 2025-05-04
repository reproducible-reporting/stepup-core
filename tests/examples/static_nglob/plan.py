#!/usr/bin/env python3
from stepup.core.api import copy, glob, runsh

# This double loop mimics the conversion and concatenation of sections and chapters
paths_ch = []
for m_ch in glob("ch-${*ch}-${*name}/", ch="[0-9]", _required=True):
    print(f"Planning ch {m_ch.ch} {m_ch.name}")
    paths_sec = []
    ngm_sec = glob(
        m_ch[0] / "sec-${*ch}-${*sec}-${*name}.txt", ch=m_ch.ch, sec="[0-9]", _required=True
    )

    for m_sec in ngm_sec:
        # Mimic compilation of a section with copy from txt to md
        print(f"Planning sec {m_sec.sec} {m_sec.name}")
        path_sec = m_sec[0][:-3] + "md"
        copy(m_sec[0], path_sec)
        paths_sec.append(path_sec)

    # Mimic concatenation of sections with cat
    path_ch = m_ch[0] / f"ch-{m_ch.ch}-compiled.md"
    runsh("cat ${inp} > ${out}", inp=paths_sec, out=[path_ch])
    paths_ch.append(path_ch)

# Mimic concatenation of chapters with cat
runsh("cat ${inp} > ${out}", inp=paths_ch, out=["book.md"])
