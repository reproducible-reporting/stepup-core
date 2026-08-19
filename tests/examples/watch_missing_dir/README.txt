A static input whose directory no longer exists is watched without recreating anything.

After a restart, the workflow still knows about `sub/inp.txt`,
while neither the file nor its directory `sub` is present.
StepUp used to recreate `sub` at startup, just to have something to watch.
It now leaves the file system alone and remembers the directory instead,
installing the watch when `sub` reappears,
so restoring the input during the watch phase is still noticed.
