One plan declares `static("data/")`; another calls `glob("../data/*.txt")` and uses a
match as a step input. The build succeeds: after Phase 2, `glob()` is a pure query that
owns nothing, so there is nothing for the tree to collide with, even though the pattern
matches inside another step's static tree.
This is the counterexample to keep the same-creator rule from being read as "trees and
glob patterns never mix": only another `static()` declaration inside someone else's tree
is an error (see `static_tree_two_plans_tree_first` and `static_tree_two_plans_file_first`).
