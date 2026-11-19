A static file that the plan no longer declares leaves the workflow,
but its directory is not removed, even when it is empty.
The directory notes/ belongs to the user,
and StepUp only removes the directory of one of its own outputs.
