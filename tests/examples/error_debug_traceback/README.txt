The same mistake as in `error_short_traceback`, but with STEPUP_DEBUG=1.
Nothing is shortened then: the client reports the director-side traceback inside an RPCError,
and its own traceback keeps StepUp's frames and the frames that launched the step.

This is the escape hatch a user is told to reach for when reporting a problem,
so it must keep working exactly as it did before the shortening was introduced.
