#!/usr/bin/env python3
from stepup.core.api import pool, runsh

pool("transform", 1)
# The file "r.txt" is used as a model for a resource of which there is only one.
runsh("echo 1; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", pool="transform")
runsh("echo 2; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", pool="transform")
