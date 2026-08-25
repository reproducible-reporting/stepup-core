# Named Glob

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

Conventional glob patterns support a handful of different wildcards.
For advanced use cases, StepUp also supports an in-house extension called "named glob".
A *named wildcard* is written as `${*name}`,
and repeating the same name within one pattern constrains both occurrences
to match the same substring.
For example, the following pattern will only match files with matching strings at the placeholders.

```text
prefix_${*name}_something_${*name}.txt
```

The following will match:

```text
prefix_aaa_something_aaa.txt
prefix_bbb_something_bbb.txt
```

The following won't:

```text
prefix_aaa_something_bbb.txt
prefix_bbb_something_aaa.txt
```

Named globs are especially valuable for coordinating output across multiple directories
where a consistent naming pattern links them together.

## Restricting What a Named Wildcard May Match

By default, a named wildcard matches `*`, i.e. anything a plain wildcard would match.
The keyword arguments of [`glob()`][stepup.core.api.glob] override that sub-pattern
for individual names:

```python
ng = glob("ch${*ch}/sec${*ch}_${*sec}_${*name}.txt", ch="[0-9]", sec="[0-9]")
```

Here, `ch` and `sec` may only match a single digit, while `name` still matches anything.
This is what makes the example below ignore inconsistently named files.

## Accessing the Captured Substrings

Iterating over the result of `glob()` yields
[`NamedGlobMatch`][stepup.core.nglob.NamedGlobMatch] objects
when the pattern contains named wildcards,
and plain `Path` objects when it does not.
The captured substrings are attributes of the match,
and `match.single` is the matching path:

```python
for match in ng:
    print(match.ch, match.sec, match.single)
```

A few methods are useful when the default iteration mode is not what you need:

- `ng.matches()` and `ng.files()` force iteration over `NamedGlobMatch` objects
  or over `Path` objects, respectively.
- `ng.single()` asserts that there is exactly one match and returns its path.

## Matching Directories by Name

When you only need to discover directories by name, without declaring a static tree or
the whole directory, combine a named wildcard with a file inside each directory that
shares its name.
For example, given a set of Typst documents organized one per directory:

```text
typst-report/report.typ
typst-summary/summary.typ
```

the directories can be discovered by name with:

```python
ng = glob("typst-${*name}/${*name}.typ")
static(ng)
for match in ng:
    ...  # match.name is "report", "summary", ...
```

This matches a *file*, not the directory itself,
so it works with or without a static tree,
while `match.name` still gives you the enclosing directory's name.

## Example

Example source files: [`docs/getting_started/named_glob/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/named_glob)

In the example below, each directory represents a chapter from course notes,
containing source files for individual sections.
The files in this example are just placeholders and the operations in the workflow are just mockups,
but the structure is realistic enough to illustrate the use of named globs.
In a realistic setting, one could envision building PDF presentations from LaTeX sources instead,
using [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/stable/)
to drive your LaTeX or Typst builds.

Create the following directory layout with markdown files:

```text
ch1/
ch1/sec1_1_introduction.txt
ch1/sec1_2_objectives.txt
ch2/
ch2/sec2_1_mathematical_requisites.txt
ch2/sec2_2_theory.txt
ch3/
ch3/sec3_1_applications.txt
ch3/sec3_2_discussion.txt
ch4/sec4_1_summary.txt
```

Create the following `plan.py`:

```python
{% include 'getting_started/named_glob/plan.py' %}
```

Note that the substrings matching the named glob patterns are accessible as attributes of
the [`NamedGlobMatch`][stepup.core.nglob.NamedGlobMatch] object.
For example, `match.ch` is the chapter number (as a string).

Make the plan executable and run StepUp:

```bash
chmod +x plan.py
sb -j 1
```

You should get the following output:

```text
{% include 'getting_started/named_glob/stdout.txt' %}
```

## Notes on `static()`

Two details of the interaction with [`static()`][stepup.core.api.static]
are worth spelling out:

- **Named wildcards work in `static()` too**,
  and the back-reference constraint applies there as well,
  so `static("ch${*ch}/sec${*ch}_*.txt")` is a valid declaration.
  There are limits, though: `static()` takes no keyword arguments,
  so a named wildcard there always uses the default sub-pattern `*`,
  and the captured substrings are not part of the return value.
  Go through `glob()` when you need either.

- **`static()` accepts the return value of `glob()` directly.**
  The call `static(ng)` declares exactly what `ng` matched,
  without registering the pattern a second time,
  because [`glob()`](glob.md) already did that.
  This is the composition used in the example plan above,
  and it is what makes query-then-declare, and the
  [probe-then-declare](glob_conditional.md) idiom, cheap.
