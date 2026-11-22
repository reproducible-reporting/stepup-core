A download step declared with `recheck=True` runs at every start of the director.
It reads a stand-in for a remote server, the directory `external/`, which the workflow does not declare,
and rewrites its output only when the external data differ from it.
When the output stays the same, its consumer is checked and skipped.
