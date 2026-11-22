#!/usr/bin/env python3
"""Look up the citation for the key in `key.txt` and write it to `citation.txt`.

Offline, write a placeholder, which the next build online replaces.
"""

from stepup.core.api import is_offline

with open("key.txt", encoding="utf-8") as fh:
    key = fh.read().strip()
if is_offline(declare=True):
    citation = f"[{key}: citation not available offline]\n"
else:
    with open(f"external/{key}.txt", encoding="utf-8") as fh:
        citation = fh.read()
with open("citation.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write(citation)
