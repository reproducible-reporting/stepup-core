A static tree declared before a file it contains, both in a single call:
`static("src/", "src/foo.txt")`.
Directory arguments are always registered before file arguments within one call,
so the file argument is a silent no-op, since the tree already owns the file.
src/foo.txt is still usable as a step input,
resolved lazily through the tree the first time it is needed.
