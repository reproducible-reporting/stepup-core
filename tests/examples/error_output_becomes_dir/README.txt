A step output that does not exist yet when the plan runs, but that the step's own command
turns into a directory instead of a regular file, fails just that one step gracefully,
like any other step failure.
