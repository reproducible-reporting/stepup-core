A directory glob() match with no static tree declared: `glob("sub/*/")`.
As of the glob()-as-pure-query refactor, a directory match no longer has to lie inside
a static tree, so this build succeeds.
The match (`sub/leaf/`) is never justified by a static declaration, so the end-of-phase
check reports it as a warning; the build still succeeds, since a warning only sets the
WARNING bit of the return code, never the FAILED one.
