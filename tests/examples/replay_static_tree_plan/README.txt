Check that when a static tree is orphaned by removing the static() call directly from
plan.py and then restored, consumer steps of files inside the static tree are correctly
skipped (not re-executed) if the files have not changed.
