Watch mode: glob("data/*.txt") in the first plan matches an undeclared file, which
the end-of-phase check reports as a warning without failing the build. The plan is
then replaced with one that declares the match static, and after it re-runs in the
same watch session, the warning is gone -- checked directly, since the two phases
share one director and .stepup/warning.log is wiped at the start of every new
build phase.
