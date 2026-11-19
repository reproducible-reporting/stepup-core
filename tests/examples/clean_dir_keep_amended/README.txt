A static file that a step no longer amends as an input leaves the workflow,
but its directory is not removed, even when it is empty.
The directory data/sub/ belongs to the user, inside the static tree data/,
and StepUp only removes the directory of one of its own outputs.
