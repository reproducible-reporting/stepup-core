#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Extract the release notes from docs/changelog.md into notes.md.

The tag (vX.Y.Z) is taken from GITHUB_REF and the repository from GITHUB_REPOSITORY.
Both are set by the GitHub Actions runner.
"""

import os
import re
from pathlib import Path

prefix = "refs/tags/v"
ref = os.environ["GITHUB_REF"]
if ref.startswith(prefix):
    version = ref.removeprefix(prefix)
    tag = f"v{version}"
else:
    version = "Unreleased"
    tag = "unreleased"

# Notes are the introduction of the changelog section "## [version] ..." up to the next heading.
changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
pattern = rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^#|\Z)"
match = re.search(pattern, changelog, re.DOTALL | re.MULTILINE)
notes = match.group(1).strip() if match else ""

owner, slug = os.environ["GITHUB_REPOSITORY"].split("/")
url = f"https://{owner}.github.io/{slug}/changelog/#{tag}"
link = f"See [docs/changelog/#{tag}]({url}) for more details."
Path("notes.md").write_text(f"{notes}\n\n{link}\n" if notes else f"{link}\n", encoding="utf-8")
