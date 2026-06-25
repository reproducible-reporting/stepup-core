#!/usr/bin/env -S bash -x
source ../example.rc

# First run: copy plan1.py as plan.py and start. The leading GREETING=hello
# assignment is applied as an environment override (even with shell=False),
# so the step writes "hello".
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &
stepup wait
stepup graph current_graph1

[[ -f greeting.txt ]] || exit 1
grep hello greeting.txt
[[ ! -f .stepup/fail.log ]] || exit 1

# Switch to plan2.py (GREETING=world) and rerun. Because the override is part of
# the step hash, the new value is detected and the step is rerun, writing "world".
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2
stepup join
wait

[[ -f greeting.txt ]] || exit 1
grep world greeting.txt
! grep -q hello greeting.txt
[[ ! -f .stepup/fail.log ]] || exit 1
