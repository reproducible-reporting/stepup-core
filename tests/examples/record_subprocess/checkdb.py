#!/usr/bin/env python3

import json
import sqlite3

con = sqlite3.connect(".stepup/graph.db")
rows = list(con.execute("SELECT seq, cmd, workdir, env_overrides, returncode FROM step_subprocess"))
con.close()

# Exactly one subprocess was recorded by the wrapper step.
assert len(rows) == 1, rows
seq, cmd, workdir, env_overrides, returncode = rows[0]

# The sequence starts at 0 and the command is stored as a plain shell command line.
assert seq == 0, seq
assert cmd == "awk 'BEGIN { print ENVIRON[\"GREETING\"] }'", cmd

# The workdir is stored relative to STEPUP_ROOT, and the run succeeded.
assert workdir == ".", workdir
assert returncode == 0, returncode

# Only the environment overlay the wrapper explicitly set is stored, not the full environment.
assert json.loads(env_overrides) == {"GREETING": "hello"}, env_overrides
