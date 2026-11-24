#!/usr/bin/env python3
import json

from stepup.core.api import amend, get_amended_info
from stepup.core.cattrs import json_converter

amend(inp="data.txt", out="result.txt")
with open("data.txt", encoding="utf-8") as fh:
    data = fh.read()
with open("result.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write(data.upper())
print(json.dumps(json_converter.unstructure(get_amended_info())))
