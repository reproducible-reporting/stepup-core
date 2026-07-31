<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
# Glob Conditional

A wildcard-free pattern passed to [`glob()`][stepup.core.api.glob] is a convenient
existence probe: `glob()` never raises for a zero-match pattern, so it can be used
directly in a conditional expression.
Declare the match with [`static()`][stepup.core.api.static] only once the probe
succeeds:

```python
from stepup.core.api import glob, static

if glob("dataset/bigfile.txt"):
    # The file exists: declare it and run plan A.
    static("dataset/bigfile.txt")
    ...
else:
    # The file is not available: run plan B instead.
    ...
```

A similar conditional would not work with `static()` directly,
because it would raise an exception when the file does not exist.

The probe is always well-behaved under the
[end-of-phase check](glob.md#end-of-build-detection-warning):
when the file exists, the same branch that probes also declares it with `static()`,
and when it does not exist, there is no match at all and nothing to justify.

## Example

Example source files: [`docs/getting_started/glob_conditional/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/glob_conditional)

Let's simulate a scenario where `dataset/`, if it exists, is remote storage with a huge dataset.
Plan A is to extract useful information from the dataset.
However, there may be reasons why this is not always possible or desirable:

- Not all your collaborators may have access to this storage at all times.
- The extraction is slow or expensive otherwise.

Plan B is to use the results of the extraction from a previous run and declare them as static files.

Create the following `plan.py`:

```python
{% include 'getting_started/glob_conditional/plan.py' %}
```

For this example, the script `expensive.py` is not expensive at all.
It just serves as an illustration of a more realistic scenario
where this script may do some non-trivial work.
In this example, `expensive.py` just computes the average of all numbers in `dataset/bigfile.txt`
and writes out the result to `average.txt`:

```python
{% include 'getting_started/glob_conditional/expensive.py' %}
```

Now put some values in `dataset/bigfile.txt`, e.g.:

```text
{% include 'getting_started/glob_conditional/dataset/bigfile.txt' %}
```

To run the example, make the scripts executable and fire up StepUp:

```bash
chmod +x expensive.py plan.py
sb -j 1
```

You should get the following output:

```text
{% include 'getting_started/glob_conditional/stdout1.txt' %}
```

Now, simulate the situation where the dataset is absent by renaming the directory:

```bash
mv dataset tmp
sb -j 1
```

The new output reveals that the dataset is completely ignored
while the file `average.txt` is still used:

```text
{% include 'getting_started/glob_conditional/stdout2.txt' %}
```

Since the file `average.txt` did not change, the step `cat average.txt` is skipped.

## Practical Considerations

- For simplicity's sake, the example involves few calculations.
  In a more realistic setting, the step `cat average.txt` is replaced by several scripts that
  create visualizations of the information extracted from the large dataset.
  Tweaking these visualizations for clarity usually takes some iterations,
  for which access to the large dataset is not necessary.

- A StepUp project practically always resides in a Git repository.
  While the files extracted from the large dataset can be reproduced easily,
  it may still be relevant to commit them into the Git repository:

    - Not all collaborators may have access to the dataset,
      but you still want them to be able to reproduce a part of the workflow.

    - In the long run, the large dataset might be removed
      because it is too big and old to keep around.
      The extracted data then become a relevant and compact subset
      that can be easily stored for longer periods.
