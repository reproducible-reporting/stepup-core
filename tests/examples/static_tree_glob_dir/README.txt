A directory glob() match inside a static tree: `static("src/"); glob("src/*/")`.
The match succeeds and is returned, but no file node is created for the directory itself,
since directories are never declared, only checked against static-tree coverage.
