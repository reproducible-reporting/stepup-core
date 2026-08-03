A step declares out.txt as an output before glob("*.txt") is registered. out.txt
already exists on disk, as if left over from a previous run, so the pattern's
filesystem scan sees it. Eager check (a), in register_nglob, rejects a pattern that
matches a file another step already builds. See glob_output_after for the mirror
scenario, which must produce the exact same message text.
