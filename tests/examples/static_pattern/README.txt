Static files can be declared with a glob pattern passed to static(), which returns the
list of paths it covers. This mirrors static_glob's reactivity check, but through
static() instead of glob().
