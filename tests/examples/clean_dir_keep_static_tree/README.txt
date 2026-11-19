An output that is left behind in a directory that the new plan declares as a static tree
is adopted by that tree as a static file, so StepUp removes neither the file nor its directory.
Because a static tree never contains an output, the removal of empty directories,
which walks up from the directories of the removed outputs, never reaches a static tree.
