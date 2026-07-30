#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan for the first time: a step builds data/foo.txt, which is then copied to
# result.txt by a second step.
cp plan1.py plan.py
sb -j 1 -w -e & # > current_stdout1.txt &

# Run StepUp for the first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/foo.txt ]] || exit 1
[[ -f result.txt ]] || exit 1
grep hello data/foo.txt
grep hello result.txt

# Between runs, drop the step that built data/foo.txt from the plan and declare a static
# tree over its directory instead, so data/foo.txt is adopted as a leftover build product
# rather than a genuine source file. At the same time, change the file's content as if it
# had been edited by something other than StepUp while it was not running, but restore the
# original mode/mtime/size/inode exactly, so a cheap stat()-based comparison cannot tell the
# new content apart from the old one. This isolates the bug under test: only clearing the
# stored hash on this BUILT -> UNCONFIRMED recycle forces a real re-hash of the new content;
# trusting the stale hash (from when the removed step built the file) would let the stat
# comparison hide the change, and the copy step below would wrongly stay skipped.
rm .stepup/*.log
cp plan2.py plan.py
python3 - <<'EOF'
import os

path = "data/foo.txt"
st = os.stat(path)
with open(path, "w") as f:
    f.write("world\n")
os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
EOF

sb -j 1 -w -e & # > current_stdout2.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/foo.txt ]] || exit 1
[[ -f result.txt ]] || exit 1
grep world data/foo.txt
grep world result.txt
