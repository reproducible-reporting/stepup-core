#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
unset STEPUP_DEBUG
# The build of the documentation may itself run offline.
unset STEPUP_BUILD_OFFLINE

# The directory external/ stands in for a server, which no step declares.
mkdir external
printf '%s\n' 2.0 4.0 > external/data.txt
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout1.txt

# The external data did not change.
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout2.txt

# The external data changed.
echo 9.0 >> external/data.txt
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout3.txt

# The external data did not change.
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout4.txt

# The external data changed, while working offline.
echo 3.0 >> external/data.txt
sb --no-progress -j 1 --offline | sed -f ../../clean_stdout.sed > stdout5.txt

# Back online.
sb --no-progress -j 1 | sed -f ../../clean_stdout.sed > stdout6.txt

# INP: plan.py
# INP: download.py
# INP: report.py
