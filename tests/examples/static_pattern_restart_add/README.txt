A pattern registered by static() must make the plan step pending after a restart, when
a new matching file appears. Mirrors restart_add_nglob for static() instead of glob().
