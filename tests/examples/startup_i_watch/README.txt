In watch mode, `STEPUP_STARTUP_I` keeps its value for the lifetime of the director.
A step that tracks it runs in the first build phase,
and not again in a later build phase that a change of an unrelated file starts.
