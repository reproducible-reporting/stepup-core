<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Asciinema Recordings for StepUp

This directory contains autocast inputs for automated Asciinema recordings of the StepUp demos.
They allow for an easy reproduction and update of the recordings.

The instructions below were tested with Asciinema version 3.

## Installation of requirements

1. Install [autocast](https://github.com/k9withabone/autocast)

    ```bash
    cargo install autocast
    ```

    Ensure that the `~/.cargo/bin` directory is in your `PATH`.

2. Install [Asciinema](https://asciinema.org/)

    ```bash
    sudo dnf install asciinema
    ```

## Recording of the template repository

Create the recording with the following command:

```bash
./template-runall.sh
```

Set the thumbnail frame to 5 seconds.

Description for Asciinema recording:

```markdown
A demonstration of the interactive use of StepUp in a template repository.

- First, StepUp is started with 4 parallel jobs.
  It is used to complete the steps in the RepRep publication template from scratch.
- After the build has completed, the file `generate.py` is modified,
  whose output is used in a matplotlib plot created by `plot.py`,
  whose output is included in a LaTeX document, etc.
- Stepup sees the changes.
  As soon as the user presses `r`,
  StepUp executes the necessary steps to rebuild all artifacts
  that are (indirectly) affected by the change in `generate.py`.
- Finally, the user presses `q` to exit StepUp.

See [StepUp RepRep documentation](https://reproducible-reporting.github.io/stepup-reprep/)
for more details.

(This recording was created with StepUp Core 4.0.0 and StepUp RepRep 4.0.0)
```

## Recording of the documentation examples

Create the recording with the following command:

```bash
./docs-runall.sh
```

Set the thumbnail frame to 5 seconds.

Description for Asciinema:

```markdown
This example is meant to give a quick visual impression of the terminal user interface of StepUp.

See [StepUp Core documentation](https://reproducible-reporting.github.io/stepup-core/) for more details.

(This recording was created with StepUp Core 4.0.0)
```

Online recordings:

- `v2.0.0`: <https://asciinema.org/a/656610>
- `v3.0.0`: <https://asciinema.org/a/718833>
- `v4.0.0`: <https://asciinema.org/a/pJXDBvCXT9ndHDuo>

## Recording for the interactive tutorial

Create the recording with the following command:

```bash
./interactive-runall.sh
```

Set the thumbnail frame to 10 seconds.

Description for Asciinema:

```markdown
This is a simple demonstration of the interactive use of StepUp.

- First StepUp is started with two workers to complete the steps from scratch.
- Then the file `src/foo.txt` is modified and the key `r` is pressed to run the affected steps.
- Afterward, the file `src/spam.txt` is created and the key `r` is pressed again.
Finally, `q` is pressed to exit StepUp

See StepUp Core tutorial [getting_started/interactive_usage/](https://reproducible-reporting.github.io/stepup-core/getting_started/interactive_usage/)

(This recording was created with StepUp Core 4.0.0)
```

Online recordings:

- `v2.0.0`: <https://asciinema.org/a/656524>
- `v3.0.0`: <https://asciinema.org/a/718834>
- `v4.0.0`: <https://asciinema.org/a/1264451>
