# Automatic Cleaning

StepUp follows the same cleanup strategy as [tup](https://gittup.org/tup/index.html):
If a step is removed or modified so that an output file is no longer created,
StepUp will remove this output.
This is also similar to [Ninja](https://ninja-build.org/)'s `cleandead` command, but it is not optional in StepUp.

The main advantage of automatic cleaning is that it eliminates potential bugs and confusion related to old output files that are no longer relevant.


## Try the Following

To illustrate the automatic cleanup, take the files from the example [Copy and mkdir](copy_mkdir.md)
and start StepUp in interactive mode.
Make the following changes and rerun the affected steps after each point by pressing `r` in the terminal:

- Change the directory `sub/` to `foo/` in `plan.py`.
  Rerunning StepUp will not only create `foo/` and `foo/hello.txt`.
  After completing all pending steps, `sub/` and `sub/hello.txt` are removed.

- Change all occurrences of `hello.txt` in `plan.py` to `hi.txt`.
  Rerunning StepUp will not only create `hi.txt` and `foo/hi.txt`.
  After completing all pending steps, `hello.txt` and `foo/hello.txt` are removed.

- Undo all changes and rerun StepUp again.
  You should end up with the original outputs without any leftovers from the previous two steps.
