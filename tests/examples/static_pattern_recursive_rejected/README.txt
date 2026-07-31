static("sub/**") is rejected before any globbing happens: a recursive ** wildcard is not
supported by static(). The error message points at static("dir/") as the replacement.
