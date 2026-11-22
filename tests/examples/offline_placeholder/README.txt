The step `cite.py` looks up a citation in a stand-in for a remote server, the directory `external/`.
Offline, it writes a placeholder instead,
and it calls `is_offline(declare=True)` to make the citation depend on the offline mode.
A second start offline runs nothing,
and the first start online replaces the placeholder with the citation.
