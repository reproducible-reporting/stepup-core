# Manual Cleaning

The automatic cleaning discussed [in a previous tutorial](../getting_started/automatic_cleaning.md)
is StepUp's default mechanism for removing irrelevant and unwanted files.
In exceptional situations, the automatic cleaning may not be sufficient,
and a more manual solution may be required.
Examples of such situations are:

- A dependency has changed that StepUp cannot track,
  such as an upgrade of a system-wide tool used in your workflow.
  In this situation, StepUp will not make the affected steps pending,
  and you may want to remove the affected outputs to rebuild them.

- You are planning a rather drastic reorganization of your project,
  including renaming some directories containing output files.
  When directories are renamed,
  StepUp can no longer clean up old outputs in the renamed directories.
  In this situation, you will want to clean up outputs manually before renaming directories.

Simply removing outputs with the `rm` command is possible
but quickly becomes tedious for larger projects.

The `stepup clean` tool can selectively remove numerous outputs with minimal end-user effort.
You need to pass as arguments the files whose (indirect) outputs you want to remove.
Such arguments can be one of the two things:

1. If a file is given, that file and all outputs using this file (indirectly) as input
   are considered for removal.

1. If a directory is given, that directory and all contained outputs are considered for removal.

When running `stepup clean` without options, it will only print which files would be removed,
without actually deleting anything.
To actually delete files, you need to pass the `--commit` or `-c` option.

From the files that are considered for removal, one can further select which files to delete.

- Without the `--all` option, only files that are no longer produced by any step
  are removed, similar to the automatic cleaning of StepUp.

- With the `--all` option, all the selected output files are removed.

The removal itself has a safety check:
`stepup clean` compares the hash of a file to the last known hash
to make sure it does not remove files that have been modified since they were created by the workflow.
This check can be disabled with the `--unsafe` option.

The `stepup clean` tool only removes files or directories.
It never changes StepUp's workflow database.
Hence, removed files remain in StepUp's workflow as orphaned nodes,
until one runs `stepup boot` (with `--clean` or `STEPUP_CLEAN=1`)
to trigger automatic cleaning.

!!! note

    In StepUp 3.0.0, the old `cleanup` was renamed to `stepup clean`.

## Try the Following

- The [Static Named Glob](static_named_glob.md) tutorial provides a good test case
  for experimenting with `stepup clean`.
  For this example, run `stepup boot` without any arguments.
  Then open a second terminal in the same directory and run
  `stepup clean ch3/sec3_1_applications.txt --all`.
  You will see that the following files have been deleted:

    - `ch3/sec3_1_applications.md`
    - `public/ch3.md`

- In the same way as in the previous point, try removing all outputs with `stepup clean . --all`.
