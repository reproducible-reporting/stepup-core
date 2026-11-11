#!/usr/bin/env python3
import os

with open("greeting.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(os.environ["GREETING"], file=fh)
