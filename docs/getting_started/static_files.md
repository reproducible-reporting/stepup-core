# Static Files

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

When steps use input files written by *you*, or at least you somehow provided these input files,
this must be explicitly stated in `plan.py`.
All files that are guaranteed to be available before StepUp starts must be declared as *static files*.
This informs StepUp that such files are readily available,
unlike files that are outputs of steps that still need to be executed.

The adjective *static* reflects the fact that these files are fixed during the build.
Any step requiring static files can be executed immediately, without waiting.
StepUp will ensure that no steps will overwrite static files,
protecting your manual files from being accidentally deleted or modified.
It is therefore beneficial to declare static files early in `plan.py`,
so StepUp knows what to protect before running any steps.

## Example

Example source files: [`docs/getting_started/static_files/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/static_files)

Create a file `limerick.txt` with the following contents:

```text
{% include 'getting_started/static_files/limerick.txt' %}
```

Also create the following `plan.py`:

```python
{% include 'getting_started/static_files/plan.py' %}
```

The [`static()`][stepup.core.api.static] function declares a static file,
i.e. one that you have created.

Make the plan executable and run it with StepUp as follows:

```bash
chmod +x plan.py
sb -j 1
```

You should get the following output:

```text
{% include 'getting_started/static_files/stdout.txt' %}
```

As expected, StepUp does not wait for another step to create `limerick.txt` because the file is static.
The file `numbered.txt` will contain a copy of the limerick with line numbers.

Keep in mind that a file can only be declared static once,
so it is always clear which step has created the static file.
When the creating step is later removed, the static files it created are also dropped from the plan.

Listing every input file by name quickly becomes tedious.
The [next tutorial](static_patterns.md) shows how a glob pattern passed to `static()`
declares a whole group of files at once.

## Try the Following

- Replace `gloom` by `boom` in `limerick.txt` and run `sb -j 1` again.
  The line numbering is repeated, but the step `./plan.py` is skipped as it did not change.

- Change the order of `static()` and `run()` in `plan.py` and run `sb -j 1` again.
  This has no apparent effect, but the step is only started after the director
  is informed that the file `limerick.txt` is static.

- Comment out the `static()` function call and run `sb -j 1` again.
  StepUp will refuse to execute the line numbering step and will show a warning explaining why.
