An example of the --postpone-cap livelock guard: a step keeps amending an
input that is never created, so it is repeatedly postponed every time a
separate, ordinary static input (trigger.txt) is edited. It is failed once
it has been postponed more times than the cap allows.
