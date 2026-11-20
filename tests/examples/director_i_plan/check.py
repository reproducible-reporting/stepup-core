#!/usr/bin/env python3
"""Declare the upload step only when the remote differs from `local.txt`."""

import hashlib

from stepup.core.api import run

with open("local.txt", "rb") as fh:
    local = fh.read()
try:
    with open("remote/data.txt", "rb") as fh:
        remote = fh.read()
except FileNotFoundError:
    remote = None
if local != remote:
    # The digest of the difference is part of the command,
    # so that StepUp never takes this step as done for another difference.
    digest = hashlib.sha256(repr((local, remote)).encode()).hexdigest()[:8]
    run(f"./upload.py {digest}", inp="local.txt")
