# Static Glob

In addition to the `static()` function in the previous two tutorials,
StepUp also supports globbing for static files.

The [`glob()`][stepup.core.api.glob] function is similar to [`static()`][stepup.core.api.static]
and supports globbing, including some non-standard glob techniques
discussed in the following tutorials.

Here, only the basic usage of [`glob()`][stepup.core.api.glob] is covered.
In the [following tutorial](static_glob_conditional.md),
the use of `glob()` in conditionals is discussed.
See [Static Named Glob](../advanced_topics/static_named_glob.md) for more advanced use cases.

## Example

Example source files: [`docs/getting_started/static_glob/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/static_glob)

Create a subdirectory `src/` with two files: `src/foo.txt` and `src/bar.txt`.
Also, create a `plan.py` file with the following contents:

```python
{% include 'getting_started/static_glob/plan.py' %}
```

Make the plan executable and run it non-interactively:

```bash
chmod +x plan.py
sb -j 1
```

This should produce the following output:

```text
{% include 'getting_started/static_glob/stdout.txt' %}
```

Note that all files found by the `glob()` function are declared static in the workflow.
Hence, they cannot be outputs of other steps.
(This restriction is part of StepUp's design.)

## Inherent Risks

Glob patterns are inherently error-prone and we therefore recommend to avoid them when possible,
and use them with care otherwise.
When using `glob()` to construct a long list of matching files,
a small number of omissions can easily go unnoticed.
For instance, files may be missing due to data loss or because of mistakes in the dataset,
and any globbing pattern will proceed with the files that are found, without any warning.

Consider for example a dataset where filenames have a predictable structure,
e.g. an enumeration as follows:

```text
file_000.txt
file_001.txt
file_002.txt
file_003.txt
```

There are two ways to loop over these files:

```python
from stepup.core.api import glob

for path in glob("file_*.txt"):
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
but the call to `static()` will fail in case of a missing file.
A globbing pattern will not.

## Try the Following

- Run StepUp again without making any changes.
  You will notice that the `./plan.py` step is executed again despite not having changed it.
  When StepUp starts from scratch, it must assume that new files matching the glob pattern
  may have been added since the last run.
  Hence, a step calling the `glob()` function can never be skipped.
  (This can be avoided when using StepUp interactively. More on that later.)

- Add a file `src/egg.txt` and run StepUp again with the same arguments.
  You will notice that known steps for `src/foo.txt` and `src/bar.txt` are skipped.
  A new step is added for `src/egg.txt`.

Never use glob functions from other libraries such as Python's built-in `glob` and `pathlib` modules.
When you use these in your `plan.py`, StepUp will not know which patterns are used,
and hence will not rerun a step when new files are added that match the glob pattern.
