A file declared before the static tree that contains it, both by the same step:
`static("src/foo.txt")`, a step consuming it, then `static("src/")`.
Same creator, so this is a no-op for the build:
src/foo.txt is handed over to the tree instead of `register_static_tree` raising.
The consumer between the two declarations makes the hand-over observable in the graph:
src/foo.txt keeps its consumer and its hash, but its creator changes from
`step:./plan.py` to `st:src/`.
See `static_tree_two_plans_file_first` for the same order across two different steps,
where it raises instead.
