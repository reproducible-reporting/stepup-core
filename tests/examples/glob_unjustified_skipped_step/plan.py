#!/usr/bin/env python3
from stepup.core.api import glob

# Nothing declares data/a.txt static. On a restart with no changes, this plan step
# is skipped and register_nglob() is not called again -- the warning must still
# fire, since check_glob_matches() reads the persisted nglob table, not what ran
# this phase.
list(glob("data/*.txt"))
