# Asciinema Recordings for StepUp

This directory contains autocast inputs for automated Asciinema recordings of the StepUp demos.
They allow for an easy reproduction and update of the recordings.

Note that Asciinema version 2.x is required for the recordings to work.

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
- First, StepUp is started with 4 workers
  to complete the steps in the RepRep publication template from scratch.
- After the build has completed, the file `generate.py` is modified,
  whose output is used in a matplotlib plot created by `plot.py`,
  whose output is included in a LaTeX document, etc.
- Stepup sees the changes.
  As soon as the user presses `r`,
  StepUp executes the necessary steps to rebuild all artifacts
  that are (indirectly) affected by the change in `generate.py`.
- Finally, the user presses `q` to exit StepUp.

See [StepUp RepRep documentation](https://reproducible-reporting.github.io/stepup-reprep/) for more details.

(This recording was created with StepUp Core 3.0.0 and StepUp RepRep 3.0.0)
```

## Recording of the documentation examples

Create the recording with the following command:

```bash
./template-runall.sh
```

Set the thumbnail frame to 5 seconds.

Description for Asciinema:

```markdown
This a simple example use case to give a quick visual impression of the terminal user interface of StepUp.

See [StepUp Core documentation](https://reproducible-reporting.github.io/stepup-core/) for more details.
```

## Recording for the interactive tutorial

Create the recording with the following command:

```bash
./interactive-runall.sh
```

Set the thumbnail frame to 10 seconds.

Description for Asciinema:

```markdown
This a simple demonstration of the interactive use of StepUp.

See StepUp Core tutorial [getting_started/interactive_usage/](https://reproducible-reporting.github.io/stepup-core/getting_started/interactive_usage/)
```
