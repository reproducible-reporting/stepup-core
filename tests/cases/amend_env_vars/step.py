#!/usr/bin/env python
import os

from stepup.core.api import amend

amend(
    inp=["${INP_VAR_TEST_STEPUP_FOO}.txt"],
    out=["${INP_VAR_TEST_STEPUP_BAR}.txt"],
    vol=["${INP_VAR_TEST_STEPUP_BAR}.log"],
)
foo = os.getenv("INP_VAR_TEST_STEPUP_FOO")
bar = os.getenv("INP_VAR_TEST_STEPUP_BAR")
with open(f"{foo}.txt", "rb") as fh:
    data = fh.read()
with open(f"{bar}.txt", "wb") as fh:
    fh.write(data)
with open(f"{bar}.log", "wb") as fh:
    fh.write(os.urandom(10))
