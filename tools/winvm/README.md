<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Windows Test VM

`winvm.sh` runs a disposable Windows 11 Enterprise LTSC evaluation VM
in a rootless Podman container, using the [dockur/windows](https://github.com/dockur/windows) image.
It installs Git, uv and an OpenSSH server in the VM,
so that you can run the test suite on Windows from your Linux shell.

## Requirements

- A Linux host with KVM, where your user can read and write `/dev/kvm`.
- Rootless Podman with the `crun` runtime.
- About 40 GB of free disk space and, by default, 4 CPU cores and 8 GB of RAM for the VM.

All VM data is stored in `~/.local/share/stepup-winvm`.
Run `tools/winvm/winvm.sh help` to see the environment variables that change the defaults.

## Creating the VM

```bash
tools/winvm/winvm.sh create
```

This command does the following:

1. Pull the dockur image.
2. Start the container, which downloads the Windows 11 LTSC evaluation ISO (4.7 GB) from Microsoft
   and installs Windows unattended.
3. At the first login, run `oem/setup.ps1` in the VM,
   which downloads and installs Git, uv and the OpenSSH server.
4. Wait until the VM accepts SSH logins.

The container logs are shown in the terminal while this runs.
The web viewer at <http://127.0.0.1:8006> shows the download progress and the Windows desktop,
including a console window with the output of `setup.ps1`.
Pressing Ctrl-C only stops watching.
The VM keeps running, and `tools/winvm/winvm.sh wait` resumes watching.

## Testing a Development Branch

Run this from the root of the repository:

```bash
tools/winvm/winvm.sh test -k "not test_example"
```

The `test` command first syncs the working tree to the VM,
and then runs `uv run pytest` with the given arguments in the VM checkout.
The sync copies the current commit plus all uncommitted and untracked changes,
except for ignored files.
It works by committing a snapshot to the ref `refs/winvm/sync` in your local repository,
without touching your branch or index,
and sending it as a Git bundle over SSH.
In the VM, the snapshot is checked out in `%USERPROFILE%\<repository name>`,
followed by `uv sync --extra dev`.
The sync works for any Git repository,
so you can run the script from a checkout of another StepUp package too.

The VM has no C compiler,
so every compiled dependency needs a Windows wheel for the Python version in the VM.
For this reason, the script pins the Python version with `WINVM_PYTHON` (default 3.14),
instead of letting uv pick the newest release.
Set it to test another version, e.g. the oldest supported one:

```bash
WINVM_PYTHON=3.11 tools/winvm/winvm.sh test -k "not test_example"
```

Other ways to work in the VM:

```bash
# Sync without running tests.
tools/winvm/winvm.sh sync
# Run a PowerShell command in the VM checkout, without syncing.
tools/winvm/winvm.sh run "uv run python -c 'import stepup.core'"
# Open an interactive PowerShell session.
tools/winvm/winvm.sh ssh
```

To connect with VS Code Remote-SSH, add the following line to `~/.ssh/config`
and connect to the host `stepup-winvm`:

```text
Include ~/.local/share/stepup-winvm/ssh_config
```

## Practical Notes

- The fast suite takes a few minutes, and the examples about 10 minutes on 4 cores,
  against about 5 minutes for the examples on Linux.
  Select the examples with `-k test_example`,
  and a few of them with `-k "test_example[no_static] or test_example[restart_add_missing]"`.
- Do not run two `winvm.sh test` commands at the same time,
  for the same reason that `CLAUDE.md` forbids concurrent `pytest` runs,
  and because the VM shares the CPUs of the host.
- Test names copied from a Windows log end in a carriage return.
  Strip it before passing them to `-k` or as node ids, or the `pytest-xdist` workers crash.
- Pytest shows captured output escaped in the VM (`\n`, `\uXXXX`),
  because an SSH session has no UTF-8 console.
- Tests that need a console (`tests/test_console.py`) create one of their own,
  so they work from an SSH session.
  Programs that need the desktop, such as `mintty`, do not:
  SSH sessions run in session 0, where they start nothing.
  Start them from a scheduled task with `/IT`, in the desktop session of the VM.
- The managed Pythons of `uv` are used, so `uv run --no-project --python 3.11 ...` also works.
  Use it with `WINVM_PYTHON` to test the oldest and the newest supported version.
- The VM has no C compiler, so a dependency without a Windows wheel for the chosen Python
  fails to install.
  Before adding a Python version to the CI matrix, check that it has wheels for all dependencies.

## Managing the VM

```bash
tools/winvm/winvm.sh stop      # Shut down Windows.
tools/winvm/winvm.sh start     # Boot it again and wait for SSH.
tools/winvm/winvm.sh destroy   # Delete the container and all VM data.
```

The evaluation edition expires after 90 days.
After that, run `destroy` followed by `create` to get a fresh VM.
Microsoft licenses the evaluation for testing, not for production use.

## Differences from a Typical Windows Setup

- The VM user `Docker` (password `admin`) is an administrator, and UAC is disabled.
  Operations that need elevated privileges, such as creating symbolic links,
  succeed in the VM but may fail for regular users.
- Git checks out files with LF line endings (`core.autocrlf=false`),
  so that the files are identical to those on Linux.
  GitHub's Windows runners have `core.autocrlf=true`, which the VM does not reproduce.
  The repository has a `.gitattributes` that makes every checkout use LF,
  so that the VM and the runners agree.
- The VM tests the working tree that `sync` copies from Linux,
  never a checkout made by Windows Git, so it cannot reveal what a Windows checkout changes.
- The VM has no Graphviz, no `python3` wrapper and no WSL.
  Run the documentation examples (`stepup build` in `docs/`) only after installing Graphviz
  and the `python3` wrapper of `docs/platforms.md`,
  as the workflow `.github/workflows/smoke.yaml` does.
- The user profile is excluded from real-time scanning by Microsoft Defender,
  which would otherwise slow down the tests considerably.

## Troubleshooting

- If `create` never finishes, open the web viewer.
  When `setup.ps1` fails, its console window stays open with the error,
  and the full log is in `C:\OEM\setup.log`.
- `podman logs stepup-winvm` shows the output of the container.
- The web viewer and the SSH port only listen on `127.0.0.1`.
  The web viewer requires no password,
  so anyone with a shell on your machine can use the VM desktop.
