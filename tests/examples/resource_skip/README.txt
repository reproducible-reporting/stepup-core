Verify that steps with resource requirements are skipped on restart even when
the resource is no longer available. Hash-checking (the CHECKING state) does
not consume named resource slots, so skipping is never blocked by resources.
