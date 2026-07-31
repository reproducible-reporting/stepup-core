A static tree declared by one plan, and a file inside it declared by another:
the root plan declares `static("data/")`, then runs `sub/plan.py`, which declares
`static("../data/foo.txt")`.
Different creators, so this raises in both orders (see `static_tree_two_plans_file_first`
for the file-first order), unlike the single-step case in `static_tree_file_after`, where
the same declarations from one step are a no-op.
The sub plan's step fails; the root plan's step, which only declared the tree and started
the sub plan, still succeeds.
