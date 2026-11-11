#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Manage a disposable Windows 11 LTSC test VM in a rootless Podman container.
# See README.md in this directory for an overview.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NAME=${WINVM_NAME:-stepup-winvm}
STATE_DIR=${WINVM_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/$NAME}
IMAGE=${WINVM_IMAGE:-docker.io/dockurr/windows:6.06}
SSH_PORT=${WINVM_SSH_PORT:-2222}
WEB_PORT=${WINVM_WEB_PORT:-8006}
CPU_CORES=${WINVM_CPU_CORES:-4}
RAM_SIZE=${WINVM_RAM_SIZE:-8G}
DISK_SIZE=${WINVM_DISK_SIZE:-64G}
WAIT_TIMEOUT=${WINVM_WAIT_TIMEOUT:-7200}
# The VM has no C compiler, so every compiled dependency needs a Windows wheel for this version.
# Without a pin, uv picks the newest Python, for which such wheels are often still missing.
PYTHON=${WINVM_PYTHON:-3.14}

SSH_CONFIG="$STATE_DIR/ssh_config"
# Written by oem/setup.ps1 after all test tools are installed.
DONE_MARKER='C:\OEM\setup-done.txt'
# Appended to a remote PowerShell command, so its failure reaches the exit code of ssh.
PS_EXIT="; if (\$?) { exit 0 } elseif (\$LASTEXITCODE) { exit \$LASTEXITCODE } else { exit 1 }"

usage() {
    cat << EOF
Usage: $(basename "$0") COMMAND [ARGS]

Commands:
  create        Create the container, install Windows and the test tools in it.
  wait          Show the container logs until the VM accepts SSH logins.
  start         Boot the VM of an existing container.
  stop          Shut down the VM.
  destroy       Delete the container and all VM data.
  ssh [CMD]     Open a PowerShell session in the VM, or run CMD in it.
  sync          Copy the working tree of the current Git repository into the VM.
  test [ARGS]   Sync, then run 'uv run pytest ARGS' in the VM.
  run CMD       Run the PowerShell command CMD in the VM checkout, without syncing.

Settings (environment variables, with their current values):
  WINVM_DIR=$STATE_DIR
  WINVM_IMAGE=$IMAGE
  WINVM_SSH_PORT=$SSH_PORT
  WINVM_WEB_PORT=$WEB_PORT
  WINVM_CPU_CORES=$CPU_CORES
  WINVM_RAM_SIZE=$RAM_SIZE
  WINVM_DISK_SIZE=$DISK_SIZE
  WINVM_PYTHON=$PYTHON
EOF
}

die() {
    echo "winvm: $*" >&2
    exit 1
}

info() {
    echo "winvm: $*" >&2
}

container_exists() {
    podman container exists "$NAME"
}

container_running() {
    [ "$(podman container inspect --format '{{.State.Running}}' "$NAME" 2> /dev/null)" = true ]
}

# Run a PowerShell script in the VM.
# Extra arguments are passed to ssh as options.
# The script travels base64-encoded,
# because no quoting survives both the ssh command line and the PowerShell parser intact.
remote_ps() {
    local script encoded
    script=$(printf '%s\n%s\n%s' \
        "\$ProgressPreference = 'SilentlyContinue'" "\$env:UV_PYTHON = '$PYTHON'" "$1")
    encoded=$(printf '%s' "$script" | iconv -f UTF-8 -t UTF-16LE | base64 -w 0)
    shift
    ssh -F "$SSH_CONFIG" -n "$@" "$NAME" \
        "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass" \
        "-EncodedCommand $encoded; exit \$LASTEXITCODE"
}

vm_ready() {
    remote_ps "if (Test-Path '$DONE_MARKER') { exit 0 } else { exit 1 }" \
        -o BatchMode=yes -o ConnectTimeout=5 > /dev/null 2>&1
}

repo_name() {
    basename "$(git rev-parse --show-toplevel)"
}

# Quote each argument as a PowerShell string literal.
ps_quote() {
    local arg q="'"
    for arg in "$@"; do
        printf " '%s'" "${arg//$q/$q$q}"
    done
}

write_ssh_config() {
    cat > "$SSH_CONFIG" << EOF
Host $NAME
    HostName 127.0.0.1
    Port $SSH_PORT
    User Docker
    IdentityFile "$STATE_DIR/id_ed25519"
    IdentitiesOnly yes
    UserKnownHostsFile "$STATE_DIR/known_hosts"
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    LogLevel ERROR
EOF
}

# dockur copies this folder to C:\OEM and runs install.bat after installing Windows.
stage_oem() {
    local oem="$STATE_DIR/oem" file
    mkdir -p "$oem"
    for file in install.bat setup.ps1; do
        # The repository stores LF line endings, while cmd.exe expects CRLF.
        sed -e 's/\r*$/\r/' "$SCRIPT_DIR/oem/$file" > "$oem/$file"
    done
    cp "$STATE_DIR/id_ed25519.pub" "$oem/authorized_keys"
}

cmd_create() {
    local avail since
    [ "$(id -u)" -ne 0 ] || die "run this as a regular user, so the container is rootless"
    for cmd in podman ssh scp ssh-keygen git iconv base64; do
        command -v "$cmd" > /dev/null || die "missing command: $cmd"
    done
    [ -r /dev/kvm ] && [ -w /dev/kvm ] || die "/dev/kvm is not readable and writable for $USER"
    container_exists && die "container $NAME exists already, use 'start' or 'destroy'"

    mkdir -p "$STATE_DIR/storage"
    # Copy-on-write fragments VM disk images badly on btrfs.
    # This fails harmlessly on other file systems.
    chattr +C "$STATE_DIR/storage" 2> /dev/null || true
    avail=$(df --output=avail -B1G "$STATE_DIR" | tail -n 1)
    if ((avail < 40)); then
        info "warning: only ${avail}G free in $STATE_DIR, while the VM may grow to 40G"
    fi
    if [ ! -f "$STATE_DIR/id_ed25519" ]; then
        ssh-keygen -q -t ed25519 -N '' -C "$NAME" -f "$STATE_DIR/id_ed25519"
    fi
    write_ssh_config
    stage_oem

    info "pulling $IMAGE"
    podman pull "$IMAGE"
    since=$(date --iso-8601=seconds)
    # Rootless containers cannot create a bridge,
    # so dockur falls back to user-mode networking (passt),
    # which forwards only the guest ports listed in USER_PORTS.
    podman run --detach --name "$NAME" \
        --device /dev/kvm \
        --group-add keep-groups \
        --publish "127.0.0.1:$WEB_PORT:8006" \
        --publish "127.0.0.1:$SSH_PORT:22" \
        --env VERSION=11l \
        --env CPU_CORES="$CPU_CORES" \
        --env RAM_SIZE="$RAM_SIZE" \
        --env DISK_SIZE="$DISK_SIZE" \
        --env NETWORK=passt \
        --env USER_PORTS=22 \
        --volume "$STATE_DIR/storage:/storage:Z" \
        --volume "$STATE_DIR/oem:/oem:Z" \
        --stop-timeout 120 \
        "$IMAGE" > /dev/null
    wait_ready "$since"
}

# Follow the container logs until the VM accepts SSH logins and its setup is complete.
wait_ready() {
    local since=${1:-} deadline=$((SECONDS + WAIT_TIMEOUT))
    container_running || die "container $NAME is not running"
    info "web viewer: http://127.0.0.1:$WEB_PORT"
    info "showing container logs until Windows is ready"
    info "Ctrl-C only stops watching, the VM keeps running and '$0 wait' resumes"
    if [ -n "$since" ]; then
        podman logs --follow --since "$since" "$NAME" &
    else
        podman logs --follow "$NAME" &
    fi
    # Global, because the EXIT trap may run after this function's locals are gone.
    LOGS_PID=$!
    trap 'kill "$LOGS_PID" 2> /dev/null || true' EXIT
    until vm_ready; do
        container_running || die "container $NAME stopped, see: podman logs $NAME"
        ((SECONDS < deadline)) || die "VM not ready after ${WAIT_TIMEOUT}s, check the web viewer"
        sleep 30
    done
    kill "$LOGS_PID" 2> /dev/null || true
    wait "$LOGS_PID" 2> /dev/null || true
    trap - EXIT
    echo
    info "the VM is ready"
    info "  run tests:       $0 test -k 'not test_example'"
    info "  open a shell:    $0 ssh"
    info "  plain ssh/scp:   ssh -F $SSH_CONFIG $NAME"
}

cmd_wait() {
    wait_ready ""
}

cmd_start() {
    local since
    container_exists || die "container $NAME does not exist, use 'create'"
    since=$(date --iso-8601=seconds)
    podman start "$NAME" > /dev/null
    wait_ready "$since"
}

cmd_stop() {
    podman stop --time 120 "$NAME" > /dev/null
}

cmd_destroy() {
    local answer
    read -r -p "Delete container $NAME and all VM data in $STATE_DIR? [y/N] " answer
    [[ $answer == [yY] ]] || die "aborted"
    podman rm --force --time 120 "$NAME" > /dev/null 2>&1 || true
    # The container may create files owned by subordinate UIDs.
    if [ -e "$STATE_DIR" ]; then
        podman unshare rm -rf "$STATE_DIR"
    fi
}

cmd_ssh() {
    exec ssh -F "$SSH_CONFIG" "$NAME" "$@"
}

cmd_sync() {
    local top name work commit tree
    top=$(git rev-parse --show-toplevel) || die "sync must run inside a Git repository"
    name=$(basename "$top")
    work="$STATE_DIR/sync"
    mkdir -p "$work"

    # Commit a snapshot of the working tree, including untracked files that are not ignored,
    # without touching the real index or any branch.
    cp "$(git -C "$top" rev-parse --path-format=absolute --git-path index)" "$work/index"
    GIT_INDEX_FILE="$work/index" git -C "$top" add --all
    tree=$(GIT_INDEX_FILE="$work/index" git -C "$top" write-tree)
    commit=$(git -C "$top" commit-tree -p HEAD -m "winvm snapshot" "$tree")
    git -C "$top" update-ref refs/winvm/sync "$commit"
    git -C "$top" bundle create --quiet "$work/$name.bundle" refs/winvm/sync

    info "copying $name ($(git -C "$top" rev-parse --abbrev-ref HEAD) + local changes) to the VM"
    scp -F "$SSH_CONFIG" -q "$work/$name.bundle" "$NAME:$name.bundle"
    remote_ps "
\$repo = Join-Path \$env:USERPROFILE '$name'
\$bundle = Join-Path \$env:USERPROFILE '$name.bundle'
function Invoke-Git {
    git -C \$repo @args
    if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
}
if (-not (Test-Path (Join-Path \$repo '.git'))) {
    git init --quiet \$repo
    if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
}
Invoke-Git fetch --quiet --force \$bundle 'refs/winvm/sync:refs/winvm/sync'
Invoke-Git checkout --quiet --force --detach refs/winvm/sync
Invoke-Git clean --quiet --force -d
Set-Location \$repo
uv sync --extra dev
exit \$LASTEXITCODE
"
}

cmd_test() {
    cmd_sync
    remote_ps "Set-Location -ErrorAction Stop (Join-Path \$env:USERPROFILE '$(repo_name)')
uv run pytest$(ps_quote "$@")$PS_EXIT"
}

cmd_run() {
    [ $# -gt 0 ] || die "usage: $(basename "$0") run CMD"
    remote_ps "Set-Location -ErrorAction Stop (Join-Path \$env:USERPROFILE '$(repo_name)')
$*$PS_EXIT"
}

main() {
    local command=${1:-}
    [ $# -gt 0 ] && shift
    case "$command" in
        create | wait | start | stop | destroy | ssh | sync | test | run)
            "cmd_$command" "$@"
            ;;
        -h | --help | help | "")
            usage
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
