static("sub/**/*.txt") is accepted: a recursive ** wildcard is only rejected as the
final path component of a pattern. Here it appears in the middle, so the pattern is
expanded eagerly like any other, matching sub/a.txt and sub/deeper/b.txt but not
sub/c.dat.
