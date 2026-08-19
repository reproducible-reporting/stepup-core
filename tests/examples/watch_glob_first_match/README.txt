glob("data/*.txt") has zero matches at plan time, and data/ does not exist yet. Its base
directory (glob_base_dir("data/*.txt") == "data") must still be watched, so creating the
first match in watch mode marks the globbing step pending. Registering a pattern never
creates a directory, so the watcher only gets a watch on data/ once it appears.
See restart_glob_first_match for the same fix across a restart.
