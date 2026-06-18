#!/usr/bin/env python3
from stepup.core.api import run

# Step requiring two resource types simultaneously, using the string format "name1:n,name2:n".
run(
    "echo A; mv s.free s.used; mv t1.free t1.used; sleep 1; mv s.used s.free; mv t1.used t1.free",
    shell=True,
    resources="slot:1,token:1",
)
# Step requiring only one t.
run("echo B; mv t2.free t2.used; sleep 1; mv t2.used t2.free", shell=True, resources="token")
# Step requiring only the s.
run("echo C; mv s.free s.used; sleep 1; mv s.used s.free", shell=True, resources="slot")
