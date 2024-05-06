#!/usr/bin/env python
from stepup.core.api import pool, step

pool("transform", 1)
# The file "r.txt" is used as a model for a resource of which there is only one.
step("echo 1; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", pool="transform")
step("echo 2; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", pool="transform")
