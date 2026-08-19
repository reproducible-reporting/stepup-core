glob("data/*.txt") matches data/a.txt, which has no node of its own: a query creates no
graph node. Deleting data/a.txt must still make the globbing step pending, which
requires change_is_relevant to fall back to matching against registered glob patterns for a
path with no node.
