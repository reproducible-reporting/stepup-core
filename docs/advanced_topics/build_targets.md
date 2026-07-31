<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
# Build Targets

As explained in [Optional Steps](optional_steps.md),
StepUp normally builds the whole workflow instead of asking you to specify targets.
For very large workflows, it can still be convenient to build only one output
(and everything it depends on) while working on a small part of a bigger project.
`stepup build` supports this with one or more positional arguments:

```bash
stepup build path/to/output.txt
```

(`sb` is a shortcut for `stepup build` and accepts the same arguments.)

When one or more targets are given, StepUp only executes the steps needed to produce them
(and their transitive dependencies). Ordinary steps that are not needed for any of the targets
are simply left `PENDING`, without being reported at the end of the build.
[Optional steps](optional_steps.md) are treated the same way as without targets:
they only run when something else actually needs their output,
which now includes being a target itself.
Steps that define other steps (like the `plan.py` script itself,
or any step using [`call()`][stepup.core.api.call] with `planning=True`)
always run, since StepUp cannot know in advance which targets they might produce.
This also means a target does not need to exist in the workflow graph beforehand:
it may only be discovered once an earlier planning step has run.

A target set is fixed for the entire lifetime of the director process,
including every build phase of a `--watch` session.
To build a different target, restart `stepup build` with the new target(s).

A target must be a step's regular output.
It cannot name a volatile output (`vol=[...]`) or a path that resolves to a static file
(declared with [`static()`][stepup.core.api.static]
or inside a registered static tree); both raise a clear error.
If a target is never produced by any step in the workflow, this is reported as a warning
at the end of the build, instead of silently doing nothing,
and the exit code gets the warning bit (`8`) set,
see [Return Codes](../reference/returncode.md).
This warning only appears when the build phase completes normally.
If the build is interrupted or put on hold
(e.g. a step fails and `--keep-going` is not used),
the warning is suppressed, since the workflow may not be fully defined yet.

A build restricted to targets never cleans up outdated outputs,
even when every step it ran succeeded.
The steps outside the target's dependencies did not run,
so their outputs are still outdated,
and removing them would throw away results that you did not ask to rebuild.
Run `stepup build` without targets to clean up again
or perform a [manual cleanup](manual_cleaning.md) with `stepup clean`.

## Example

Example source files: [`docs/advanced_topics/build_targets/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/build_targets)

Create the following `plan.py`, which copies `input.txt` to three different outputs,
one of which is optional:

```python
{% include 'advanced_topics/build_targets/plan.py' %}
```

Make the plan executable and build only `report.txt`:

```bash
chmod +x plan.py
sb report.txt -j 1
```

You should get the following terminal output:

```text
{% include 'advanced_topics/build_targets/stdout.txt' %}
```

Only `report.txt` is created; `extra.txt` and `debug.txt` are left untouched, and no warning is printed,
as this is the expected result.

## Directory Targets

A target ending in a slash (e.g. `out/`) is a *directory target*:
it elevates every step whose declared need is `DEFAULT`
and whose regular output falls under that directory,
instead of naming one output file exactly.
The trailing slash is the **only** thing that distinguishes a directory target
from a file target; classification never looks at the file system.
A slashless target is always an exact-file target,
even when it happens to name a directory that already exists on disk.

```bash
stepup build path/to/output/
```

This is convenient when a step consumes the outputs of several other steps
that it discovers one at a time
(for example, a document format that resolves included files as it parses them).
Discovering such dependencies without making them targets can defeat concurrency:
the build would be postponed once per discovered input,
one at a time, instead of building the whole subtree up front.
To restore concurrency, you can organize your workflow
so that all outputs of a step live under a single directory,
and then target that directory instead of the individual files.

Directory targets are deliberately **best-effort**, unlike exact-file targets:

- A directory realistically mixes step outputs with source files, volatile outputs,
  and files that do not exist yet.
  A directory target therefore never raises an error for anything under it,
  no matter its state; it simply elevates whatever is already eligible.
  A typo'd directory or an empty subtree silently builds nothing extra,
  rather than failing the build outright,
  though a warning is still printed at the end of the build
  when a directory target matches no regular output at all
  (the same not-produced warning as for exact targets, but per directory).
- A directory target only reaches steps whose declared need is `DEFAULT`.
  Unlike an exact-file target, it does **not** reach `optional` steps,
  even when their output lives under the targeted directory
  (see [Optional Steps](optional_steps.md) for the asymmetry).
  Naming a file explicitly is a stronger signal than it merely sitting in a target directory.
- Targeting a directory does not require it to exist yet, on disk or in the workflow graph.
  A clean checkout where the directory is created by the first step that runs
  is the normal case.

Exact-file and directory targets can be combined in a single invocation.
An exact target that happens to lie under a directory target
is then subject to both regimes at once, and the strict one wins:
naming the file explicitly still raises an error
when it matches a static or volatile file,
and still triggers the not-produced warning,
even though the directory target alone would have stayed silent about it.

Because directory-target elevation only looks at a step's own outputs,
a step whose regular outputs are all volatile, or that has no regular outputs at all,
can never be elevated this way, just as it can never be named by an exact target.
Such steps only run when something else that is actually built depends on them.

## Command-Line Pitfalls

Two limitations of the argument parser are worth knowing when combining targets with options:

- Targets must all come before or all after the option flags.
  For example, `sb a.txt -j 2 b.txt` is rejected by the argument parser,
  while `sb a.txt b.txt -j 2` and `sb -j 2 a.txt b.txt` both work.

- When combining targets with `--perf`, write `--perf=FREQ`
  (e.g. `sb --perf=500 wanted.txt`).
  A bare `--perf` takes an optional value
  and would consume the first target as the profiling frequency.

- Forgetting the trailing slash on a directory target is not an error:
  the directory name is interpreted as an exact-file target instead,
  which simply ends in a "not produced by any step" warning
  instead of building the subtree.

## Try the Following

- Run `sb -j 1` again, without a target.
  This time, `extra.txt` is created, but `debug.txt` remains skipped
  because it is `optional` and nothing else needs it.

- Run `sb debug.txt -j 1`.
  Even though `debug.txt` is `optional`, targeting it explicitly is enough to build it.
  Being a target elevates the need of its producing step, regardless of the step's declared need.

- Run `sb nope.txt -j 1`, targeting a file that no step produces.
  The build ends with a warning that `nope.txt`
  was not produced by any step, and a non-zero exit code (bit `16`).

- Run `sb input.txt -j 1`.
  This fails with a `GraphError`, because `input.txt` is a static file, not a step's output.
