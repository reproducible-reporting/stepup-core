<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Documentation

Note that docstrings are written in Markdown, not reStructuredText!
The docstring conventions themselves are in the top-level `CLAUDE.md`.

## Documentation Examples

Each `docs/getting_started/<example>/` directory contains a `main.sh`
that generates `stdout.txt` (the terminal output shown in the tutorial page).
To regenerate after changing example scripts, run:

```bash
cd docs/getting_started/<example>
bash main.sh
```

This runs StepUp locally and captures the output via `sed -f ../../clean_stdout.sed`.
Commit the updated `stdout.txt` alongside any source changes.
