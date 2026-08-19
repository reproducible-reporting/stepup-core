An output file removed by hand leaves its directory behind. When a new plan.py no longer
builds that output, StepUp still removes the directory, because the file node names its
parent as a candidate for removal whatever the state of the file itself.
See clean_dir_stale for the case where the directory is removed by hand as well.
