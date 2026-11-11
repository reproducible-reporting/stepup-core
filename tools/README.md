<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Developer Tools

Scripts for developing StepUp Core, which are not part of the installed package.

- `docs/make_stable_redirects.py`:
  Write redirect stubs from the retired `stable/` URLs into the built documentation site.
- `imports/leaves.py`:
  Print the modules of a package in layers,
  starting with those that import no other module of the package.
- `macci/`:
  Run the test suite on a GitHub-hosted macOS runner and print its output locally.
  See `macci/README.md`.
- `perf/analyze_perf.py`:
  Summarize the profiling output of a StepUp run
  (yappi profile, SQL log, `perf.data` and job log), read from the current directory.
  The profiling output files in `perf/` are ignored by Git.
- `winvm/`:
  Run the test suite in a Windows 11 VM in a rootless Podman container.
  See `winvm/README.md`.
