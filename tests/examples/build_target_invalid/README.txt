A build target can never legitimately name a volatile output or a static file -- both are
rejected with a `GraphError` as soon as the offending declaration is made. This example
covers both cases: targeting a `vol_path` (checked in `define_step`, ahead of `Step.reattach`)
and targeting a path that resolves to a static file declared via `static()` (checked in
`_declare_file`).
