static("nothing*.txt") matches nothing in this directory. Zero matches is not an
error: the pattern is still registered, so a later run can react to a match that
appears afterwards.
