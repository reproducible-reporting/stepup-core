#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect(".stepup/graph.db")
stdout, stderr = con.execute(
    "SELECT stdout, stderr FROM step_output "
    "JOIN node ON node.i = step_output.node WHERE node.label = './work.py'"
).fetchone()
con.close()
assert "python-stdout-line" in stdout, stdout
assert "subprocess-stdout-line" in stdout, stdout
assert "subprocess-stderr-line" in stderr, stderr
