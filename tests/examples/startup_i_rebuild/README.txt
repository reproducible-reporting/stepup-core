In watch mode, `stepup rebuild --recheck` gives `STEPUP_STARTUP_I` a new value,
so the steps with `recheck=True` run again, as after a new start of the director.
A plain `stepup rebuild` leaves them alone.
The steps see the new value in their environment,
both in a shell step and in a Python script, which runs in the forkserver when it is enabled.
