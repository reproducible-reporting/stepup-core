Steps depending on each other will be executed sequentially in the right order,
even when multiple workers are available.

When rewriting the boot script, new steps will be executed and outdated outputs are removed.

This example also demonstrates that StepUp normalizes all paths it receives.
