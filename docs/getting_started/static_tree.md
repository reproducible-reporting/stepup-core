# Static Tree
<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

The previous tutorials declared static files one by one or with a glob pattern.
Both approaches enumerate the files, which is impractical for a directory
holding a large or unpredictable number of them.
To declare static files in bulk, you can pass a directory path to the `static()` function,
which marks the entire directory tree as static.
This tutorial shows how to use this method.

## Example

Example source files: [`docs/getting_started/static_tree/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/static_tree)

Create a subdirectory `data/` with one file: `somefile.txt`.
Also, create a `plan.py` file with the following contents:

```python
{% include 'getting_started/static_tree/plan.py' %}
```

Make the plan executable and run it non-interactively:

```bash
chmod +x plan.py
sb -j 1
```

This should produce the following output:

```text
{% include 'getting_started/static_tree/stdout.txt' %}
```

Under the hood, StepUp will not immediately declare all files in the `data/` directory as static.
Instead, they are declared static lazily when they are first accessed.
This means that `data/` may contain a huge number of files without causing any performance issues.

Calling `static("data/")` and then `static("data/somefile.txt")` is fine:
the second call is a no-op, since the tree already owns the file.
The reverse order, `static("data/somefile.txt")` first and `static("data/")` second,
is also fine: the tree takes ownership of the file that was already declared.
Both orders are no-ops because they come from the same step; the point of the rule is
that a single owner exists regardless of which declaration happened to run first.
Within a single `static()` call, directory arguments are always registered before
file arguments, so `static("data/", "data/somefile.txt")` and
`static("data/somefile.txt", "data/")` are both fine and behave identically:
argument order within one call does not matter.

A *different* step declaring `data/somefile.txt` is a different matter: a static tree
is the sole owner of the files under it, so that raises an error, in either
declaration order.
(You can use [the `glob()` function](glob.md) discussed in the next tutorial
to list the files inside another step's static tree without claiming them.)

## Static Trees from Glob Patterns

A static tree is also what you get when a
[glob pattern](static_patterns.md) matches a directory:
the directory is registered as a static tree, exactly as if it had been listed by name.
Spell such a pattern with a trailing slash so the intent is visible at the call site:

```python
static("data/*/")
```

This also explains why `static()` rejects a *trailing* recursive `**` wildcard,
e.g. `data/**` or bare `**`.
Such a pattern would try to enumerate an entire subtree eagerly merely to declare it
as static, which is precisely what a static tree does lazily and much more efficiently.
Declare the tree instead:

```python
static("data/")             # instead of static("data/**")
```

A `**` earlier in the pattern, e.g. `static("data/**/*.txt")`, is still accepted:
it isn't a stand-in for a whole-tree declaration,
since the file suffix still constrains the match.

When a recursive *list* of the files is genuinely needed in `plan.py`,
declare the tree first and then query it with [`glob()`](glob.md).

## Try the Following

- Add a file `data/egg.txt` and run StepUp again with the same arguments.
  Inspect the database with `stepup browse` and notice that `data/egg.txt` is completely ignored
  because it was never used as an input.

- Try to copy `data/somefile.txt` to `data/otherfile.txt` with the `copy()` function
  and run StepUp again.
  You will notice that this fails because `data/` is only allowed to contain static files,
  not outputs of steps.
