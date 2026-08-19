glob("data/*.txt") has zero matches at plan time, but data/ already exists. Its base
directory (glob_base_dir("data/*.txt") == "data") must still be watched via
watch_existing_dir, so creating the first match in watch mode marks the globbing step
pending. See restart_glob_first_match for the same fix across a restart.
