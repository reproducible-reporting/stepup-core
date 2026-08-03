An example of the --defer-cap livelock guard: a step keeps amending an
input that is never created, so it is repeatedly deferred every time a
separate, ordinary static input (trigger.txt) is edited. It is failed once
it has been deferred more times than the cap allows.
