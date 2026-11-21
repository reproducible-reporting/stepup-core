#!/usr/bin/env python3
"""Download `data.txt` from the remote, unless the local copy is up to date."""

import hashlib
import os
import shutil
import sys

REMOTE = "remote/data.txt"
LOCAL = "data.txt"


def checksum(path: str) -> str | None:
    """Return the SHA-256 checksum of a file, or `None` if it does not exist."""
    try:
        with open(path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def main():
    local = checksum(LOCAL)
    if os.environ.get("STEPUP_BUILD_OFFLINE") == "1":
        if local is None:
            sys.exit(f"Working offline, so {LOCAL} cannot be downloaded.")
        print(f"Working offline, keeping {LOCAL}.")
        return
    # This mimics the usage of a research-grade data server, like iRODS,
    # which can report the checksum, so downloads of unchanged data can be skipped.
    if checksum(REMOTE) != local:
        shutil.copyfile(REMOTE, LOCAL)


if __name__ == "__main__":
    main()
