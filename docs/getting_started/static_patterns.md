---
description: >-
  Declare many static files at once by passing a glob pattern to static(),
  which returns the sorted matches and accepts a pattern that matches nothing.
---

# Glob Patterns in `static()`

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

In addition to explicit paths, the [`static()`][stepup.core.api.static] function also
accepts glob patterns with the usual anonymous wildcards (`*`, `?`, `[abc]`),
so you don't have to list every file by name.
Every match is declared exactly as if you had listed it by name.

The return value is a sorted list of the paths the call covers,
which makes a one-liner the common idiom:

```python
for path in static("src/*.txt"):
    copy(path, "out/" + path.name)
```

A pattern without matches is not an error.
Unlike a literal path, which must exist, a pattern that matches nothing is accepted as is,
for a good reason explained in the next section.

## Example

Example source files: [`docs/getting_started/static_patterns/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/static_patterns)

Create a subdirectory `src/` with two files: `src/foo.txt` and `src/bar.txt`.
Also, create a `plan.py` file with the following contents:

```python
{% include 'getting_started/static_patterns/plan.py' %}
```

Make the plan executable and run it non-interactively:

```bash
chmod +x plan.py
sb -j 1
```

This should produce the following output:

```text
{% include 'getting_started/static_patterns/stdout.txt' %}
```

## Patterns Make Steps React to New and Deleted Files

A pattern does more than save you some typing.
Besides declaring the matches, StepUp records the *pattern itself* with the step that
called `static()`, together with the set of matches it produced.

On a later run, StepUp re-scans the file system and compares.
When a matching file was added or removed since the last build,
the step that called `static()` is made pending and runs again,
so the plan is rebuilt against the new set of files.
When the set of matches is unchanged, the step is skipped like any other.
This is also why a zero-match pattern is accepted:
it is registered anyway, so a match that appears later is still noticed.

For this to work, StepUp must know about every pattern you use.
Never use glob functions from other libraries,
such as Python's built-in `glob` and `pathlib` modules.
When you use these in your `plan.py`, StepUp will not know which patterns are used,
and hence will not rerun a step when files are added or removed that match the pattern.

## Escaping Glob Metacharacters

Because every argument of `static()` is read as a pattern,
the characters `*`, `?` and `[` are significant everywhere.
A file whose name literally contains one of them can no longer be declared by spelling it out.
Use [`glob.escape()`](https://docs.python.org/3/library/glob.html#glob.escape)
from the standard library instead:

```python
import glob as globmod

from stepup.core.api import static

static(globmod.escape("table[1].csv"))
```

(While this solves the problem of declaring a file with a metacharacter in its name,
it is generally recommended to avoid such characters in filenames altogether.)

## Directories and Recursive Patterns

Two rules of thumb complete the picture, each covered by a following tutorial:

- A match that is a directory is registered as a *static tree*,
  the subject of the [next tutorial](static_tree.md).
  Spell such a pattern with a trailing slash, e.g. `static("data/*/")`,
  so the intent is visible at the call site.

- A recursive `**` wildcard is accepted by `static()`, except as the final path
  component, e.g. `static("src/**")` and `static("**")` are rejected.
  Declare the directory as a [static tree](static_tree.md) instead, e.g. `static("src/")`,
  which covers the whole subtree lazily.
  A `**` earlier in the pattern, e.g. `static("src/**/*.txt")`, is accepted
  and expanded eagerly, the same as any other pattern.
  When a recursive *list* of files is genuinely needed instead of declaring them,
  use [`glob()`](glob.md) once the tree is declared.

## Inherent Risks

Glob patterns are inherently error-prone and we therefore recommend to avoid them when possible,
and use them with care otherwise.
When using a pattern to construct a long list of matching files,
a small number of omissions can easily go unnoticed.
For instance, files may be missing due to data loss or because of mistakes in the dataset,
and any globbing pattern will proceed with the files that are found, not warning you of potential gaps.

Consider for example a dataset where filenames have a predictable structure,
e.g. an enumeration as follows:

```text
file_000.txt
file_001.txt
file_002.txt
file_003.txt
```

The first approach is to loop over them with a pattern:

```python
from stepup.core.api import static

for path in static("file_*.txt"):
    # do something with path
```

Alternatively, one can loop over the expected range of numbers:

```python
from stepup.core.api import static

for i in range(4):
    path = f"file_{i:03d}.txt"
    static(path)
    # do something with path
```

If the four files are present, both loops are equivalent in StepUp.
The latter encodes a bit of extra knowledge about the dataset,
which requires a small effort to implement,
but the call to `static()` will fail in case of a missing file even when using a pattern.

## Try the Following

- Run StepUp again without making any changes.
  You will notice that `./plan.py` is skipped this time: on startup, StepUp re-scans the
  file system and compares it against the pattern's persisted matches, so a step is only
  made pending again when the match set actually changed.

- Add a file `src/egg.txt` and run StepUp again with the same arguments.
  You will notice that known steps for `src/foo.txt` and `src/bar.txt` are skipped.
  A new step is added for `src/egg.txt`.

- Delete the file `src/bar.txt` and run StepUp again.
  The plan is re-run because the set of matches shrank,
  and the copy step for `src/bar.txt` disappears from the workflow,
  together with its output `dst/bar.txt`.

!!! note "Changed in StepUp 4"

    In StepUp 3, `static()` only accepted literal paths:
    a pattern had to be passed to `glob()`, which declared its matches as a side effect.
    See [`static()` and `glob()` Have New Roles](../migration/from_3x_to_40.md#static-and-glob-have-new-roles)
    for the (usually one-word) edit this requires.
