#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect(".stepup/graph.db")
output = dict(con.execute("SELECT kind, content FROM step_output").fetchall())
con.close()
assert "python-stdout-line" in output.get("stdout", ""), output
assert "subprocess-stdout-line" in output.get("stdout", ""), output
assert "subprocess-stderr-line" in output.get("stderr", ""), output
