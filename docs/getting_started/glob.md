---
description: >-
  Query the static files matching a pattern with glob(),
  which returns the matches without declaring or owning any of them.
---

# Glob

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

StepUp's [`glob()`][stepup.core.api.glob] function looks like
[`static()`][stepup.core.api.static] with a pattern, but does not declare its matches static.

`glob()` is a pure *query*.
It scans the file system, records the pattern with the calling step,
and returns the matches.
It creates no node in the workflow and owns nothing it matches.
However, every match must be declared static elsewhere, either by some `static()` call,
or covered by a [static tree](static_tree.md).

## Example

Example source files: [`docs/getting_started/glob/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/glob)

Create a subdirectory `src/` with three files:
`src/intro.txt`, `src/method_notes.txt` and `src/result_notes.txt`.
Also, create a `plan.py` file with the following contents:

```python
{% include 'getting_started/glob/plan.py' %}
```

Make the plan executable and run it non-interactively:

```bash
chmod +x plan.py
sb -j 1
```

This should produce the following output:

```text
{% include 'getting_started/glob/stdout.txt' %}
```

## Overlapping Queries

The example above queries the same files twice, with two different patterns,
and this is the whole point of `glob()`.
Because a query owns nothing, any number of `glob()` calls may match the same file,
from any number of plans.
Declare once with `static()`, then query as often as convenient.

The `NamedGlob` object returned by `glob()` can be iterated directly,
as in the first query,
or converted to a plain list of paths with `ng.files()`, as in the second one.
More of its features are covered in the [Named Glob](named_glob.md) tutorial.

!!! note "Changed in StepUp 4"

    In StepUp 3, overlapping queries were impossible:
    `glob()` declared its matches, so two calls matching the same file raised an error.
    Projects worked around this with ordering tricks and non-overlapping patterns.

## Reacting to New and Deleted Files

Just like a pattern passed to `static()`,
a `glob()` pattern is registered with the calling step,
together with the set of matches it produced.
The step is made pending and re-runs when that set changes on a later run,
and is skipped when it does not.
See [Patterns Make Steps React to New and Deleted Files](static_patterns.md#patterns-make-steps-react-to-new-and-deleted-files)
for the details, which are identical for both functions.

## Match Static Files Only

StepUp will either fail or issue a warning if a `glob()` match is not declared static
because allowing such matches could lead to non-deterministic builds.
For example, an output file from an initial run could be picked up by a subsequent run
as a match and alter the workflow.
To keep your workflow deterministic,
StepUp requires every match to be declared static by a step or covered by a static tree.

There are two degrees of severity for this check,
depending on whether the match is a file that some step builds or not.

### Eager Detection -- Failure

There is a single case in which a `glob()` match is an outright error:
a matched file that some step *builds*.
Such a file is not source material and can never become static,
so the pattern is simply wrong and will result in a **failed** build.

This is reported in either declaration order,
immediately when the pattern is registered or when the output is declared,
whichever comes second.
Any occurrence that slipped through is caught by the end-of-build detection as well,
reported as `N glob match(es) are files that a step builds.`,
which also set the `FAILED` bit of the [return code](../reference/returncode.md).
Narrow the pattern, or declare the file with `static()` instead of building it.

### End-of-build Detection -- Warning

A glob match that does not match any file in the workflow cannot be judged while the build is running:
the plan that would declare it may simply not have run yet.
These unchecked matches are settled at the end of the build phase,
against whatever the workflow looks like by then.
A match that is still unjustified at that point is reported as a **warning**:

```text
  WARNING │ 1 glob match(es) are not declared static.
─────────────────────────────── ./plan.py: *.md ────────────────────────────────
           (no node)  extra.md
──────────────────────────────────── Remedy ────────────────────────────────────
Every matched file must be declared static or lie inside a static tree.
A matched directory also qualifies when it contains a static file or tree.
Declare the files with static(), or declare their directory as a static tree.
────────────────────────────────────────────────────────────────────────────────
```

The offending patterns and paths are listed per step,
and the same report is written to `.stepup/warning.log`.
This is a warning and nothing more: it does not fail the build.
It does set the warning bit (`8`) of the [return code](../reference/returncode.md),
so a script can notice it without parsing the log.
The check is also skipped entirely when the build phase already failed for another
reason, because an unjustified match could be a consequence of that failure.

Note that directories can be matched with `glob()`.
However, because directory nodes are not part of the workflow,
a directory match can only be checked at the end of the build phase.
A directory match is considered safe when it lies inside a static tree,
contains one, or contains a static file.

## Inherent Risks

A `glob()` pattern can silently under-match in exactly the same way as a `static()`
pattern: see [Inherent Risks](static_patterns.md#inherent-risks).

It is worth stressing that the split between declaring and querying does not make
globbing any safer.
It only clarifies who owns what.
E.g., a pattern that matches three files where you expected four is still a pattern that
matches three files, whichever function you pass it to.

## Try the Following

- Add a file `src/egg.txt` and run StepUp again with the same arguments.
  The first query gains a match, so `./plan.py` re-runs and a copy step is added,
  without any change to `plan.py`:
  the static tree `src/` already covers the new file.
  The concatenation step is skipped, because the second query still matches
  the same two files.

- Create a file `extra.md` and add a query for it at the end of `plan.py`:

    ```python
    glob("*.md")
    ```

    Nothing uses the match, so no step becomes pending,
    but nothing declares `extra.md` static either.
    Run StepUp again and read `.stepup/warning.log`:
    it contains the report shown above.
    Note that no step fails: `sb` exits with return code `8`,
    the warning bit, and not with the `FAILED` bit.

- Change that query to `glob("*.txt")` and run StepUp again.
  The pattern now matches `notes.txt`, which a step builds,
  so `./plan.py` fails with a `GraphError` explaining the conflict.
