Steps can be associated with a series of environment variables that they are sensitive to.
When the environment variable changes (set differently before stepup starts),
this will result in a rerun instead of a skip.

Note that changes to environment variables made in steps cannot be reliably tracked.
Environment variables can only be used to pick up settings and flags set outside StepUp.
