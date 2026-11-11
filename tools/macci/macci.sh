#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Run commands in a snapshot of the working tree on a GitHub-hosted macOS runner.
# See README.md in this directory for an overview.

set -euo pipefail

# Required, so that snapshots never end up in the main repository by default.
REMOTE=$(git config --get macci.remote || true)
RUNNER=${MACCI_RUNNER:-macos-latest}
# Pinned, so that the Python version does not depend on what the runner image provides.
PYTHON=${MACCI_PYTHON:-3.15}

WORKFLOW=macci.yaml
# The workflow prints these markers around the output of the command.
BEGIN_MARKER='macci: output begins'
END_MARKER='macci: exit status'

# Global, because the EXIT trap may run after the locals of dispatch are gone.
REPO=
BRANCH=
RUN_ID=

usage() {
    cat << EOF
Usage: $(basename "$0") COMMAND [ARGS]

Commands:
  test [ARGS]   Push a snapshot, then run 'uv run pytest ARGS' on a GitHub-hosted runner.
  run CMD       Push a snapshot, then run the Bash command CMD on a GitHub-hosted runner.
  clean         Delete snapshot branches that interrupted runs left behind on the remote.

Git configuration (required, see README.md):
  macci.remote=$REMOTE

Environment variables (with their current values):
  MACCI_RUNNER=$RUNNER
  MACCI_PYTHON=$PYTHON
EOF
}

die() {
    echo "macci: $*" >&2
    exit 1
}

info() {
    echo "macci: $*" >&2
}

check_setup() {
    local cmd
    for cmd in git gh awk sed; do
        command -v "$cmd" > /dev/null || die "missing command: $cmd"
    done
    git rev-parse --git-dir > /dev/null || die "run this inside a Git repository"
    [ -n "$REMOTE" ] || die "set the Git remote of your fork with: git config macci.remote NAME"
}

# Quote each argument for Bash, leaving simple words as they are.
bash_quote() {
    local arg
    for arg in "$@"; do
        if [[ $arg =~ ^[A-Za-z0-9_./=:,+@%-]+$ ]]; then
            printf ' %s' "$arg"
        else
            printf ' %s' "${arg@Q}"
        fi
    done
}

# Print the GitHub repository behind the remote as HOST/OWNER/NAME.
github_repo() {
    local url
    url=$(git remote get-url "$REMOTE") || die "no Git remote named $REMOTE"
    gh repo view "$url" --json url --jq '.url | sub("^https://"; "")' \
        || die "remote $REMOTE ($url) is not a GitHub repository"
}

# Commit a snapshot of the working tree, including untracked files that are not ignored,
# without touching the real index or any branch, and print its hash.
snapshot() {
    local index tree
    index=$(git rev-parse --path-format=absolute --git-path macci-index)
    cp "$(git rev-parse --path-format=absolute --git-path index)" "$index"
    GIT_INDEX_FILE="$index" git add --all
    tree=$(GIT_INDEX_FILE="$index" git write-tree)
    git commit-tree -p HEAD -m "macci snapshot" "$tree"
}

# Print the ID of the run on BRANCH, waiting until GitHub has created it.
find_run() {
    local id
    for _ in {1..30}; do
        id=$(gh run list --repo "$REPO" --workflow "$WORKFLOW" --branch "$BRANCH" \
            --json databaseId --jq '.[0].databaseId // empty')
        if [ -n "$id" ]; then
            echo "$id"
            return
        fi
        sleep 2
    done
    die "GitHub did not create a run for $BRANCH"
}

# Wait until the run has completed, reporting each change of its status.
# A failed status query is retried, so that a network hiccup does not cancel the run.
wait_run() {
    local status previous=
    while :; do
        status=$(gh run view "$RUN_ID" --repo "$REPO" --json status --jq .status 2> /dev/null) \
            || status=unknown
        [ "$status" != completed ] || return 0
        if [ "$status" != "$previous" ]; then
            info "the run is $status"
            previous=$status
        fi
        sleep 15
    done
}

# Print the log of the run, without the job and step columns and the timestamps.
# GitHub may need a few seconds after the run has completed to make the log available.
run_log() {
    local log
    for _ in {1..10}; do
        if log=$(gh run view "$RUN_ID" --repo "$REPO" --log 2> /dev/null); then
            printf '%s\n' "$log" | cut -f3- \
                | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z ?//; s/\r$//'
            return
        fi
        sleep 3
    done
    die "could not download the log of the run"
}

# Cancel the run if it is still going, and delete its snapshot branch.
cleanup() {
    local status
    if [ -n "$RUN_ID" ]; then
        status=$(gh run view "$RUN_ID" --repo "$REPO" --json status --jq .status 2> /dev/null) \
            || status=unknown
        if [ "$status" != completed ]; then
            info "cancelling the run"
            gh run cancel "$RUN_ID" --repo "$REPO" > /dev/null || true
        fi
    fi
    if [ -n "$BRANCH" ]; then
        git push --quiet --delete "$REMOTE" "$BRANCH" \
            || info "could not delete $BRANCH from $REMOTE, '$0 clean' removes it later"
    fi
}

# Push a snapshot to a new branch, run the Bash command $1 in it on the runner,
# print its output and exit with its exit status.
dispatch() {
    local command=$1 commit branch changes url conclusion log status
    check_setup
    REPO=$(github_repo)
    commit=$(snapshot)
    branch=macci/${commit:0:12}
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    info "pushing $(git rev-parse --abbrev-ref HEAD) + local changes to $REMOTE as $branch"
    changes=$(git diff --compact-summary HEAD "$commit")
    [ -z "$changes" ] || printf '%s\n' "$changes" >&2
    git push --quiet "$REMOTE" "$commit:refs/heads/$branch"
    BRANCH=$branch

    gh workflow run "$WORKFLOW" --repo "$REPO" --ref "$BRANCH" \
        --raw-field command="$command" \
        --raw-field python="$PYTHON" \
        --raw-field runner="$RUNNER" > /dev/null \
        || die "could not start $WORKFLOW, which must exist on the default branch of $REPO"
    RUN_ID=$(find_run)
    url="https://$REPO/actions/runs/$RUN_ID"
    info "running on $RUNNER: $command"
    info "$url (Ctrl-C cancels the run)"
    if [ -t 2 ]; then
        gh run watch "$RUN_ID" --repo "$REPO" --compact --interval 10 >&2 || true
    fi
    wait_run

    conclusion=$(gh run view "$RUN_ID" --repo "$REPO" --json conclusion --jq .conclusion)
    log=$(run_log)
    printf '%s\n' "$log" | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        index($0, end) == 1 { found = 0 }
        found { print }
        $0 == begin { found = 1 }
    '
    status=$(printf '%s\n' "$log" | sed -n "s/^$END_MARKER \([0-9]*\)$/\1/p")
    [ -n "$status" ] || die "the command did not finish, the run ended as $conclusion: $url"
    [ "$status" -eq 0 ] || info "the command failed with exit status $status: $url"
    exit "$status"
}

cmd_test() {
    dispatch "uv run pytest$(bash_quote "$@")"
}

cmd_run() {
    [ $# -gt 0 ] || die "usage: $(basename "$0") run CMD"
    dispatch "$*"
}

cmd_clean() {
    local refs branches
    check_setup
    refs=$(git ls-remote --heads "$REMOTE" 'refs/heads/macci/*' | cut -f2)
    if [ -z "$refs" ]; then
        info "no snapshot branches on $REMOTE"
        return
    fi
    mapfile -t branches <<< "$refs"
    info "deleting ${#branches[@]} snapshot branch(es) from $REMOTE"
    git push --quiet --delete "$REMOTE" "${branches[@]}"
}

main() {
    local command=${1:-}
    [ $# -gt 0 ] && shift
    case "$command" in
        test | run | clean)
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
