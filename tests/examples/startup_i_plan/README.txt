A planning step declared with `recheck=True` runs at every start of the director.
It compares a local file with a stand-in for a remote server, the directory `external/`,
and declares an upload step only when the two differ.
When they agree, the upload step is gone from the graph.
