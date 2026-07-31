A volatile output (vol=[...]) is also rejected by a matching glob pattern: eager check
(a) in register_glob treats a VOLATILE file the same way as an AWAITED, BUILT or
OUTDATED one.
