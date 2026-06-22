#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

stepup boot -j 1 -w & # > current_stdout.txt &
PID=$!
stepup wait
stepup join
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Confirm the warning appeared in the creator step's stderr (captured in success.log).
grep "non-planning step" .stepup/success.log
