A file declared before the static tree that contains it: `static("src/foo.txt"); static("src/")`.
The tree declaration raises a `GraphError` naming both paths,
since a static tree must be declared before any file it contains.
