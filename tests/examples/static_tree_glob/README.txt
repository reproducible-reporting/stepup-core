Two overlapping glob() calls over the same static tree: `static("src/")` followed by
`glob("src/*.txt")` and `glob("src/f*.txt")`. Both succeed, since the tree owns the matched
files, so glob() only registers the patterns instead of trying to declare the files again.
