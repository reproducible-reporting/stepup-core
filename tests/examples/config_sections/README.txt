Every subcommand reads its settings from its own section in the configuration files.

The `[build]` section configures `stepup build` and its deprecated alias `stepup boot`,
which share a single source of truth for their configuration.
The `[clean]` section configures `stepup clean`,
whose settings can also be given as `STEPUP_CLEAN_*` environment variables.
