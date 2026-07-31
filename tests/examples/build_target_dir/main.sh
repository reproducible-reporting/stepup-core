#!/usr/bin/env -S bash -x
source ../example.rc

# Static inputs are created here (instead of tracked in git) so later runs can modify them.
echo a1 > a_input.txt
echo b1 > b_input.txt
echo c1 > c_input.txt
echo other > other_input.txt

# Run 1: a single directory target covers the three producers and the consumer, all under
# out/. Producers are declared before the consumer and durations are disabled in tests
# (STEPUP_BUILD_DURATION=0), so with tied _tail_time the node-id tiebreak dispatches them
# first: the consumer's first invocation should already find all three inputs available.
sb out/ -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f out/a.txt ]] || exit 1
[[ -f out/b.txt ]] || exit 1
[[ -f out/c.txt ]] || exit 1
[[ -f out/result.txt ]] || exit 1
[[ ! -f other.txt ]] || exit 1
grep -q a1 out/a.txt
grep -q b1 out/b.txt
grep -q c1 out/c.txt
grep -qx a1b1c1 out/result.txt

# The flagship claim: the consumer succeeded on its first invocation, because directory-
# target elevation made all three producers eligible (and, given the deterministic
# dispatch order above, already built) before the consumer ever ran -- instead of one
# postponed retry per input discovered through amend().
grep -qx 1 invocations.txt

# Run 2: a directory target that matches no regular output triggers a WARNING (not a build
# failure) and sets the WARNING bit in the exit code, just like an exact-file target that is
# never produced.
rm .stepup/*.log
sb empty/ -j 1 -w & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq "${RETURN_CODE_WARNING}" ]] || exit 1

grep -F "directory target(s) matched no regular output in the workflow: empty/" .stepup/warning.log
