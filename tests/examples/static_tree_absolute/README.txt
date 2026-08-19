A static tree can be rooted at an absolute path outside the project.

The tree owns the files under it just as a relative tree does, and the file it
contains is declared static lazily, when the copy step first uses it as an input.
The temporary directory differs on every run, so neither the graph nor the
reporter output is compared against an expected file.
