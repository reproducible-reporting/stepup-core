`--fix-epoch` (the default) sets `SOURCE_DATE_EPOCH` for the steps,
unless it is set already.
A step that tracks the variable must run again when a start with `--no-fix-epoch` unsets it,
and when the next start sets it again.
This holds for a step that declares the variable in `plan.py`,
and for a step that reads it with `getenv()` while it runs.
