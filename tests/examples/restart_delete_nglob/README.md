Upon restart, StepUp checks whether any known files have been deleted.
If yes, steps using nglobs that used deleted files are made pending again,
so they are rescheduled for execution.
