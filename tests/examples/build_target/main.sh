#!/usr/bin/env -S bash -x
source ../example.rc

# Static inputs are created here (instead of tracked in git) so later runs can modify them.
echo one > input.txt
echo seed > seed.txt

# Run 1: fresh graph, target wanted.txt and dynamic.txt.
# dynamic.txt is declared by gen.py's planning step (Need.PLAN, always runs regardless of
# targets), which only runs after `call()` dispatches it -- demonstrating that a target does
# not need to be discoverable upfront. other.txt is left PENDING (DEFAULT need, below the
# targeted threshold) and must not be reported as pending.
sb wanted.txt dynamic.txt -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f wanted.txt ]] || exit 1
[[ -f dynamic.txt ]] || exit 1
[[ ! -f other.txt ]] || exit 1
grep -q one wanted.txt
grep -q seed dynamic.txt

# Run 2: full untargeted build, completing the remaining steps.
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

[[ -f other.txt ]] || exit 1
grep -q one other.txt

# Run 3: a target that is never produced by any step triggers a WARNING, not a build failure,
# but does set the WARNING bit in the exit code.
rm .stepup/*.log
sb nope.txt -j 1 -w & # > current_stdout3.txt &
PID=$!

stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq "${RETURN_CODE_WARNING}" ]] || exit 1

grep -F "target(s) are not produced by any step in the workflow: nope.txt" .stepup/warning.log
