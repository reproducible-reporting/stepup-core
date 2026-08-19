Like watch_glob_delete, but the whole data/ directory is removed instead of the single
matched file. This produces a DELETED_PARENT event, not a DELETED one: relevant_paths_under
must also yield the recorded matches of registered glob patterns under the removed
directory, or the globbing step would never be marked pending.
