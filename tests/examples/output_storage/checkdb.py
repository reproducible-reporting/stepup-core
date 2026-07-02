#!/usr/bin/env python3

import sqlite3

con = sqlite3.connect(".stepup/graph.db")
stdouts = [c for (c,) in con.execute("SELECT stdout FROM step_output")]
con.close()

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
