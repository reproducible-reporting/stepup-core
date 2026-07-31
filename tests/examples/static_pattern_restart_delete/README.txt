A pattern registered by static() must make the plan step pending after a restart, when
a matching file is deleted. Mirrors restart_delete_nglob for static() instead of glob().
