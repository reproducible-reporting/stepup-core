static("sub/${*name}.txt") accepts a named wildcard, but its captures are not part of
the flat return value. The registered pattern keeps the named wildcard verbatim, with
no subs dict, since static() takes no keyword arguments.
