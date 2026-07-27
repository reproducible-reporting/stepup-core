#!/usr/bin/env -S bash -x
source ../example.rc

# Build with the deprecated boot alias, without any command-line options.
# It shares its configuration with `stepup build`, so `jobs` and `clean` are taken from
# the [build] section in stepup.toml. The `clean = false` setting shows up in the output
# as a warning about the skipped file cleanup.
stepup boot & # > current_stdout.txt &

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/inp.txt ]] || exit 1
[[ -f sub/out.txt ]] || exit 1

# Clean up sub/, with `all` from the [clean] section in stepup.toml
# and `commit` from the environment. Without either, nothing is removed.
STEPUP_CLEAN_COMMIT=1 stepup clean sub/ > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -d sub/ ]] || exit 1
