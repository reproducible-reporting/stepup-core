# Volatile Outputs

It may happen that steps produce auxiliary outputs that are not really of interest,
but rather occur as a side effect.
For example, LaTeX is notoriously productive in terms of output files.
Some of these files will change with every run, e.g., because they contain timestamps.

It is useful to inform StepUp of the existence of such volatile files, so they can be cleaned up when appropriate.
However, there is no point in computing file hashes for them,
as these files are not used as inputs later and may change for no good reason.
One may pass a list of such files to the `vol` argument of the [`step()`][stepup.core.api.step] function.


## Example

Example source files: [advanced_topics/volatile_outputs/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/volatile_outputs)

Create the following `plan.py`, with a single step that produces a trivially volatile output:

```python
{% include 'advanced_topics/volatile_outputs/plan.py' %}
```

Make the plan executable and run it as follows:

```bash
chmod +x plan.py
stepup -n -w1
```

The file `date.txt` will contain the current time.
You should get the following terminal output:

```
{% include 'advanced_topics/volatile_outputs/stdout.txt' %}
```


## Try the Following

- Remove the file `date.txt` and run StepUp again.
  You will see that the step gets ignored:
  StepUp does not care much about the state of volatile files.
  It only keeps track of them, so they can be removed when needed.

- Manually recreate the file `date.txt` with some arbitrary contents,
  and run StepUp.
  Again, the step gets skipped because the contents of the
  volatile `date.txt` are not considered when deciding if a step is outdated.

- Comment out the step in `plan.py` and run StepUp again.
  Because the step is removed, the volatile output is also removed.
