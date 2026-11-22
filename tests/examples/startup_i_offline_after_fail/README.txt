The usual way into offline mode: a start without `--offline` fails
because the stand-in for a remote server, the directory `external/`, cannot be reached,
and the next start uses `--offline`.
`STEPUP_STARTUP_I` then keeps the value of the failed start.
The download step `pull.py` runs again because it failed, and keeps its output.
The upload step `push.py` runs because its last successful run had another value,
and it sends nothing because it is offline.
