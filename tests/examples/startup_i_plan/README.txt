A planning step that tracks `STEPUP_STARTUP_I` runs at every start of the director.
It compares a local file with a stand-in remote, the directory `remote/`,
and declares an upload step only when the two differ.
When they agree, the upload step is gone from the graph.
