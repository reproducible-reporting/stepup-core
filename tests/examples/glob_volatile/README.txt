A volatile output (vol=[...]) is also rejected by a matching glob pattern: eager check
(a) in register_nglob treats a VOLATILE file the same way as a PLANNED, BUILT or
OUTDATED one.
