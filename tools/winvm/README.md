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
