# Manual Cleaning

The automatic cleaning discussed [in a previous tutorial](../getting_started/automatic_cleaning.md)
is StepUp's default mechanism for removing irrelevant and unwanted files.
In exceptional situations, the automatic cleaning may not be sufficient,
and a more manual solution may be required.
Examples of such situations are:

- A dependency has changed that StepUp cannot track, such as an upgrade of a system-wide tool used in your workflow.
  In this situation, StepUp will not make the affected steps pending, and you may want to remove the affected outputs to rebuild them.

- You are planning a rather drastic reorganization of your project, including renaming some directories containing output files.
  When directories are renamed, StepUp can no longer clean up old outputs in the renamed directories.
  In this situation, you will want to clean up outputs manually before renaming directories.

Simply removing outputs with the `rm` command is possible but quickly becomes tedious for larger projects.
The `cleanup` program, a companion to `stepup`, can selectively remove a large number of outputs with minimal end-user effort.
You need to pass as arguments the files whose (indirect) outputs you want to remove.
Such arguments can be one of the two things:

1. If a file is given, all outputs using this file as input will be removed.
   Furthermore, if the file itself is also a build output, it will also be removed.

1. If a directory is given, all outputs will be removed from this directory.
   Furthermore, if the directory is created in the build, it will also be removed.

Files are removed recursively, so outputs of outputs are also cleaned up.
`cleanup` will only remove files with status `PENDING`, `BUILT` or `VOLATILE`.
Static files, i.e., files you have created, are never removed.

There are a few gotchas you should be aware of:

1. The `cleanup` script sends a list of paths to be cleaned to the director process.
   The director takes care of analyzing the workflow to decide which files need to be removed.
   For this reason, an instance of `stepup` must be running for `cleanup` to work.

1. By default, you need to run `cleanup` in the top-level directory where you also started `stepup`.
   This requirement can be lifted by defining the top-level directory in the `STEPUP_ROOT` environment variable, as explained in the [next tutorial](stepup_root.md).

If `cleanup` cannot connect to the StepUp director process, it will keep trying and print warning messages, for example:

```
Trying to contact StepUp director process.
File ./.stepup/logs/director not found.  Waiting 0.1 seconds.
Socket /tmp/stepup-c9a12bau/director read from ./.stepup/logs/director does not exist. Stepup not running?  Waiting 0.2 seconds.
Socket /tmp/stepup-c9a12bau/director read from ./.stepup/logs/director does not exist. Stepup not running?  Waiting 0.3 seconds.
...
```

When you see this, either start `stepup` in a second terminal or interrupt `cleanup` with Ctrl-C.

## Try the Following

- The [Static Named Glob](static_named_glob.md) tutorial provides a good test case for experimenting with `cleanup`.
  For this example, run `stepup` without any arguments.
  Then open a second terminal in the same directory and run `cleanup ch3/sec3_1_applications.txt`.
  You will see that the following files have been deleted:

    - `ch3/sec3_1_applications.md`
    - `public/ch3.md`

- In the same way as in the previous point, try removing all outputs with `cleanup ./`.
