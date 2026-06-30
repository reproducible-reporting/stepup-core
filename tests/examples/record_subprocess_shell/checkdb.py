#!/usr/bin/env python3

import sqlite3

con = sqlite3.connect(".stepup/graph.db")
rows = list(
    con.execute(
        "SELECT seq, cmd, workdir, env_overrides, returncode, stdout, stderr FROM step_subprocess"
    )
)
con.close()

# Exactly one subprocess was recorded by the wrapper step.
assert len(rows) == 1, rows
seq, cmd, workdir, env_overrides, returncode, stdout, stderr = rows[0]

# The sequence starts at 0 and the command is stored as a plain shell command line.
assert seq == 0, seq
assert cmd == "GREETING=hello; (echo ${GREETING} > out.txt)", cmd

# The workdir is stored relative to STEPUP_ROOT, and the run succeeded.
assert workdir == ".", workdir
assert returncode == 0, returncode

# Only the environment overlay the wrapper explicitly set is stored, not the full environment.
assert env_overrides is None, env_overrides

# The subprocess wrote to out.txt, not to its own stdout/stderr, so both are captured empty.
assert stdout == "", stdout
assert stderr == "", stderr
