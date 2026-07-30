A static input file that is replaced by a directory between two invocations.
StepUp cannot compute the file hash of a directory,
but the startup phase must survive it instead of taking the director down.
The error is reported with the provenance of the path,
i.e. the nodes that declare and consume it,
and the scheduler is put on hold, so nothing is built with a file StepUp cannot check.
