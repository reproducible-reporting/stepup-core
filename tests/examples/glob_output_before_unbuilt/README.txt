A step declares sub/out.txt as an output before glob("sub/*.txt") is registered,
in a fresh build where sub/out.txt does not exist yet.
Eager check (a), in register_nglob, rejects a pattern that matches a declared output,
whether that output exists on disk or not.
Otherwise, the first build would succeed and the next one would fail.
See glob_output_before for the same scenario with a stale file on disk.
