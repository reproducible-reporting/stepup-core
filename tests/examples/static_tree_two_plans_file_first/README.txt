A file declared by one plan, and a static tree containing it declared by another:
the root plan declares `static("data/foo.txt")`, then runs `sub/plan.py`, which declares
`static("../data/")`.
Different creators, so this raises in both orders (see `static_tree_two_plans_tree_first`
for the tree-first order), unlike the single-step case in `static_tree_file_handover`,
where the same declarations from one step are a no-op and the file is handed over instead.
The sub plan's step fails; the root plan's step, which only declared the file and started
the sub plan, still succeeds.
