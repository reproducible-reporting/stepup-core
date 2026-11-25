A step declares sub/out.txt as an output before glob("sub/*.txt") is registered.
Eager check (a), in register_nglob, rejects a pattern that matches a declared output.
That check is lexical, so the stale sub/out.txt on disk, as if left over from a previous run,
changes nothing.
See glob_output_before_unbuilt for the same scenario without the stale file,
and glob_output_after for the mirror scenario.
All three must produce the exact same message text.
