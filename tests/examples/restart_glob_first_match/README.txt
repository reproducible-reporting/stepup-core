Like watch_glob_first_match, but the first match is created between two directorless
phases, so it is picked up on restart via check_nglob_changes. The example then also
checks that data/ stayed watched afterward: startup.populate_dir_queue must derive that
watch from the registered pattern alone, since data/ was never the target of a
static() declaration or a previous match.
