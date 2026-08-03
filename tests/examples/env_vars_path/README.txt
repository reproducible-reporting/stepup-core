The paths in a step may contain shell variables.
These are substituted before the step is communicated with the director process.
The plan is also amended with these dynamic environment variables.

Environment variables in the command are not substituted,
because commands may be shell expressions that make use of their own variables.
When the command uses shell variables defined before StepUp is started,
they must be manually listed in the `env_vars` argument.
