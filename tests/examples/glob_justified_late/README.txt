The root plan globs sub/*.txt before sub/plan.py -- run later, as a nested plan --
declares the same files static. The match is not yet justified when the root's
glob() call runs, so this is the case that forces the unjustified-match check to
run at the end of the build phase rather than eagerly: by then sub/plan.py has
had its chance to declare the files, and the build stays green.
