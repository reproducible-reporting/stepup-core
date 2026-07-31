---
name: release
description: Release procedure for StepUp Core — update the changelog, tag the version, and push so the PyPI GitHub Action fires. Use when cutting a release, tagging a version, or publishing to PyPI.
---

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->
# Release Process

1. Update `docs/changelog.md`.
2. Commit and tag: `git tag vX.Y.Z`.
3. Push with tags: `git push origin main --tags` — triggers PyPI GitHub Action.
