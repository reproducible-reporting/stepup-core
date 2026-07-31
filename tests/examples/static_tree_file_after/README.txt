A static tree declared before a file it contains, both in a single call:
`static("src/", "src/foo.txt")`.
Directory arguments are always registered before file arguments within one call,
so this always takes the tree-first branch of the same-creator rule:
the tree is the file's owner either way,
and the outcome would be identical if the file had been declared first
(see `static_tree_file_handover` for that order).
Unlike before, the file declaration is not silently dropped:
src/foo.txt gets a graph node right away, owned by the tree,
rather than only once it is first used as a step input.
