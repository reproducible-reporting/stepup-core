#!/usr/bin/env -S bash -x
source ../example.rc

echo a1 > a_input.txt
echo b1 > b_input.txt
echo c1 > c_input.txt
echo d1 > d_input.txt

# A directory target and two exact-file targets combined in one build. The directory
# target elevates out/a.txt (declared-DEFAULT); the exact targets elevate solo.txt
# (declared-DEFAULT) and exact_optional.txt (declared-OPTIONAL -- exact targets still reach
# OPTIONAL steps). out/optional.txt stays unbuilt: its output falls under the directory
# target, but its declared need is OPTIONAL, so the directory target's declared-DEFAULT
# restriction excludes it -- the asymmetry documented in optional_steps.md.
sb out/ solo.txt exact_optional.txt -j 1 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph
stepup join
wait

[[ -f out/a.txt ]] || exit 1
[[ -f solo.txt ]] || exit 1
[[ -f exact_optional.txt ]] || exit 1
[[ ! -f out/optional.txt ]] || exit 1
grep -q a1 out/a.txt
grep -q b1 solo.txt
grep -q d1 exact_optional.txt
