An example of the --reschedule-cap livelock guard: a step keeps amending an
input that is never created, so it is repeatedly rescheduled every time a
separate, ordinary static input (trigger.txt) is edited. It is failed once
it has been rescheduled more times than the cap allows.
