A step can be associated with environment variables that it is sensitive to.
This example checks that a step records exactly the requested variable (AWDFTD)
and ignores another variable (DFTHYH) that is set in the shell but not requested.

Related examples extend this with changes to the requested set and to the values:
env_vars_config_add, env_vars_config_touch, env_vars_restart_value,
env_vars_restart_unset and env_vars_restart_set.
