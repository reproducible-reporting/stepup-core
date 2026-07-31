This example tests and demonstrates recursive globbing to get a list of files
with a specific extension in a directory tree.

`glob()` cannot declare a `**` pattern static (that is what `static()` rejects),
so `data/` is declared as a static tree up front and the recursive `glob()` calls
stay pure queries against it. This example still succeeds if `data/` were removed:
`glob()`'s Phase 2 eager checks only reject a match that is a known build product,
not one that is merely undeclared. A late check (Phase 4) is needed to catch that
case, and this example is the illustration of why.
