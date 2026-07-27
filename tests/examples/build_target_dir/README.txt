`stepup build <dir>/` restricts the build to declared-DEFAULT steps whose output falls
under that directory. This example demonstrates the concurrency this restores for a
consumer that discovers its inputs one at a time (e.g. a typst document resolving
dependencies as it parses): three independent producers and the consumer all write under
`out/`, so a single directory target elevates all of them up front. The producers are
declared before the consumer and durations are disabled in tests, so they run to
completion before the consumer's first invocation -- which therefore succeeds immediately,
instead of needing one postponed retry per discovered input. `other.txt`, outside `out/`,
is left unbuilt, showing that the directory target still scopes the rest of the build.
