#!/usr/bin/env python3
from stepup.core.api import getenv

with open("epoch_getenv.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(getenv("SOURCE_DATE_EPOCH", "unset"), file=fh)
