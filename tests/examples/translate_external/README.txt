Relative (input, output, ...) paths given to steps are translated to relative directories to `${STEPUP_ROOT}` before they are passed to the director.
This example checks the special situation that the relative directory contains a part of the parent directories of `${STEPUP_ROOT}`.
This scenario is a bit unusual but may occur when inputs or outputs refer to external directories shared with other StepUp projects.
