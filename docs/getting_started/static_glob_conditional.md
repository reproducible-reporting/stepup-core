# Static glob conditional

The [`glob()`][stepup.core.api.glob] function introduced in the previous tutorial
also works in conditional expressions.
This is particularly useful when not using any wildcards at all:

```python
from stepup.core.api import glob

if glob("dataset/"):
    # The dataset exists, is a directory and is now static.
    # Steps for plan A.
    ...
else:
    # The directory dataset is not available.
    # Steps for plan B.
    ...
```

A similar conditional would not work with the [`static()`][stepup.core.api.static] function
because it would raise an exception when the file does not exist.


## Example

Let's simulate a scenario where `dataset/`, if it exists, is remote storage with a huge dataset.
Plan A is to extract useful information from the dataset.
However, there may be reasons why this is not always possible or desirable:

- Not all your collaborators may have access to this storage at all times.
- The extraction is slow or expensive otherwise.

Plan B is to use the results of the extraction from a previous run and declare them as static files.

Create the following `plan.py`:

```python
{% include 'getting_started/static_glob_conditional/plan.py' %}
```

For this example, the script `expensive.py` is not expensive at all.
It just serves as an illustration of a more realistic scenario where this script may do some non-tritial work.
In this example, `expensive.py` just computes the average of all numbers in `dataset/bigfile.txt`
and writes out the result to `average.txt`:

```python
{% include 'getting_started/static_glob_conditional/expensive.py' %}
```

Now put some values in `dataset/bigfile.txt`, e.g.:

```
{% include 'getting_started/static_glob_conditional/dataset/bigfile.txt' %}
```

To run the example, make the scripts executable and fire up StepUp in non-interactive mode:

```bash
chmod +x expensive.py plan.py
stepup -n -w1
```

You should get the following output:

```
{% include 'getting_started/static_glob_conditional/stdout1.txt' %}
```

Now, simulate the situation the absence of the dataset by renaming the directory:

```bash
mv dataset tmp
stepup -n -w1
```

The new output reveals that the dataset is completely ignored while the file `average.txt` is still used:

```
{% include 'getting_started/static_glob_conditional/stdout2.txt' %}
```

Since the file `average.txt` did not change, the step `cat average.txt` is skipped.


## Practical considerations

- The example involves few calculations for the sake of simplicity.
  In a more realistic setting, the step `cat average.txt` is replaced by several scripts that
  make graphs of the information extracted from the large dataset.
  Tweaking these graphs for clarity usually takes some iterations,
  for which access to the big dataset is not relevant.

- A StepUp project practically always resides in a Git repository.
  While the files extract from the big dataset can be reproduced easily,
  it may still be relevant to commit the data extracted from the big data set into the Git repository:

    - Not all your collaborators may have access to the dataset,
      but you still want them to be able to reproduce the workflow.

    - In the long run, the big dataset may be removed because it is too big and old keep around.
      The extracted data are then a relevant and compact subset that you can easily store for longer times.
