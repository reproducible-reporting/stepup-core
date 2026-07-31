glob("data/*.txt") matches data/a.txt, which nothing declares static and which is
not inside a static tree. The end-of-phase check reports this as a warning, not an
error: the build still succeeds and only sets the WARNING bit of the return code.
