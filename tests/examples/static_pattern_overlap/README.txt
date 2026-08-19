Two overlapping glob patterns, static("data/*/*.txt") and static("data/sub/*.*"), both
declare data/sub/a.txt from the same plan. This is the user-facing motivation for the
same-creator no-op: without it, the second pattern would fail.
