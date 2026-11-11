<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# macOS Test Runs

`macci.sh` runs the test suite of your working tree on a GitHub-hosted macOS runner,
and prints the output in your terminal, as if pytest had run locally.
It is the macOS counterpart of `tools/winvm/`,
but it cannot use a local VM,
because Apple's license only permits macOS VMs on Apple hardware.

## Requirements

- The [GitHub CLI](https://cli.github.com/) `gh`, logged in to the account that owns your fork.
- A fork of the repository on GitHub, set up as described below.

## Setting Up Your Fork

The script pushes snapshots to your fork and runs the workflow there,
so that the main repository gets neither the temporary branches nor the workflow runs.
The following one-time steps use `USER/stepup-core` for your fork.

1. Add the fork as a Git remote of your clone, and tell the script to use it:

    ```bash
    git remote add fork git@github.com:USER/stepup-core.git
    git config macci.remote fork
    ```

    The setting is stored in `.git/config`, so it is never committed.
    The script refuses to run without it.

2. Enable GitHub Actions in the fork, which GitHub disables by default for forks:

    ```bash
    gh api --method PUT repos/USER/stepup-core/actions/permissions -F enabled=true
    ```

3. Disable all other workflows in the fork,
   because they would run on every update of the default branch of the fork:

    ```bash
    for id in $(gh workflow list --repo USER/stepup-core --json id,path --jq '.[]
        | select(.path | startswith(".github/workflows/"))
        | select(.path | endswith("/macci.yaml") | not)
        | .id'); do
        gh workflow disable "$id" --repo USER/stepup-core
    done
    ```

    Repeat this step when an update brings in a new workflow.

4. Update the default branch of the fork, so that it contains `.github/workflows/macci.yaml`:

    ```bash
    gh repo sync USER/stepup-core
    ```

    GitHub only starts manually triggered workflows that exist on the default branch.

## Running Tests

Run this from anywhere in the repository:

```bash
tools/macci/macci.sh test -k "not test_example"
```

This command does the following:

1. Commit a snapshot of the working tree,
   which contains the current commit plus all uncommitted and untracked changes,
   except for ignored files.
   This touches neither your branch nor your index.
2. Push the snapshot to a new branch `macci/<hash>` in your fork.
3. Start the workflow `macci.yaml` on that branch in your fork.
   It runs `uv sync --extra dev` followed by `uv run pytest` with the given arguments.
4. Wait for the run to complete.
   When the terminal is interactive, `gh run watch` shows the progress of the run.
5. Print the output of `uv sync` and `pytest`, and exit with the exit status of `pytest`.
6. Delete the branch.

Pressing Ctrl-C cancels the run and deletes the branch.
The run and its log remain available in the Actions tab of your fork.
Because every snapshot gets its own branch, you can start several runs at the same time.
Unlike the Windows VM, the runs do not load your own machine.

The run uses the version of `macci.yaml` in the snapshot,
so changes to the workflow itself are tested as well.

Other ways to use the runner:

```bash
# Run a Bash command in the checkout on the runner, after `uv sync --extra dev`.
tools/macci/macci.sh run "uv run python -c 'import platform; print(platform.platform())'"
# Delete the snapshot branches that interrupted runs left behind in your fork.
tools/macci/macci.sh clean
```

Use `run` only for work on this project,
because the [GitHub terms for Actions](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)
only permit hosted runners for building, testing, deploying or publishing the project itself.

You can also start the workflow from the Actions tab of your fork, for any branch in it,
with the command as an input.

## Settings

Run `tools/macci/macci.sh help` to see the current values.
Besides `macci.remote` in the Git configuration,
the following environment variables change the defaults:

- `MACCI_RUNNER` (default `macos-latest`):
  The label of the GitHub-hosted runner, e.g. `macos-15-intel` for an Intel Mac.
- `MACCI_PYTHON` (default `3.15`):
  The Python version that uv installs on the runner, e.g. `3.11` for the oldest supported one.

The script also works for other StepUp packages,
when they have a `dev` extra and a copy of `macci.yaml`,
and when you set up a fork of them in the same way.

## What Gets Published

A fork of a public repository is public,
so the snapshot publishes your uncommitted changes and untracked files.
Deleting the branch does not undo this,
because GitHub keeps serving a commit by its hash after the branch is gone.
GitHub also shares commits between a repository and its forks,
so the commit can even be opened through the URL of the main repository,
although it appears in none of its branches and none of its workflow runs.
Before pushing, the script lists the files in which the snapshot differs from your current commit.
Local commits that you have not pushed yet are published too,
as part of the history of the snapshot.
Add files that must never leave your machine to `.gitignore`.

Runs in a public fork are free.

## Troubleshooting

- The job times out after 30 minutes.
  The script then prints the output up to that point,
  and reports that the command did not finish.
- When the run fails before the command starts, e.g. during the checkout,
  the script prints nothing but the URL of the run.
  `gh run view --log-failed RUN_ID --repo USER/stepup-core` shows the log of the failed step.
- When the script cannot start the workflow,
  check the steps in [Setting Up Your Fork](#setting-up-your-fork).
