#!/usr/bin/env python3
from stepup.core.api import run

# The file "r.txt" is used as a model for a resource of which there is only one.
run("echo 1; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", shell=True, resources="transform")
run("echo 2; mv r.txt u.txt; sleep 0.1; mv u.txt r.txt", shell=True, resources="transform")
