Demonstrate step-specific environment variable overrides.

A leading `VAR=value` assignment in a `run()` command (with shell=False) is stripped
from the command and applied as an environment override when the step runs.
The override participates in the step hash, so the step is skipped on a restart
when nothing has changed.
