#!/usr/bin/env python3

import sqlite3

con = sqlite3.connect(".stepup/graph.db")
rows = con.execute("SELECT stdout, returncode, utime, stime, wtime FROM step_outcome").fetchall()
con.close()

stdouts = [row[0] for row in rows]
sentinel = "[output truncated at 64 bytes]"
# The failed step stored its output (storage is independent of success).
assert any("fail-output-line" in c for c in stdouts), stdouts
# The oversized output was truncated with the sentinel appended.
truncated = [c for c in stdouts if sentinel in c]
assert truncated, stdouts
content = truncated[0]
assert content.rstrip("\n").endswith(sentinel), repr(content)
# The body before the sentinel line stays within the 64-byte budget.
body = content.split("\n" + sentinel)[0]
assert len(body.encode("utf-8")) <= 64, len(body.encode("utf-8"))

# The return code and resource usage are persisted alongside the output.
assert any(row[1] != 0 for row in rows), rows
# Every recorded step spent measurable wall and CPU time in its child process. This must hold
# for the failing step too, which burns enough CPU (see fail.py) to stay well above the clock
# granularity: the accounting must not collapse to zero on the failure path, as it did when
# _forkserver_entry snapshotted getrusage() outside a `finally`.
assert all(row[4] > 0.0 for row in rows), rows
assert all(row[2] + row[3] > 0.0 for row in rows), rows
