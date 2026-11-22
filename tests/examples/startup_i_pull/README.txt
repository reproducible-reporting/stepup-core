A download step declared with `recheck=True` runs at every start of the director.
It reads a stand-in remote, the directory `remote/`, which the workflow does not declare,
and rewrites its output only when the remote data differ from it.
When the output stays the same, its consumer is checked and skipped.
