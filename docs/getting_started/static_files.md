# Static Files

When steps use input files written by you, this must be explicitly stated in `plan.py`
by declaring the human-written files as *static files*.
This informs StepUp that such files are readily available,
unlike files that are outputs of steps that still need to be executed.

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
stepup boot -n 1
```

You should get the following output:

```text
{% include 'getting_started/static_files/stdout.txt' %}
```

As expected, StepUp does not wait for another step to create `limerick.txt` because the file is static.
The file `numbered.txt` will contain a copy of the limerick with line numbers.

## Try the Following

- Replace `gloom` by `boom` in `limerick.txt` and run `stepup boot -n 1` again.
  The line numbering is repeated, but the step `./plan.py` is skipped as it did not change.

- Change the order of `static()` and `runsh()` in `plan.py` and run `stepup boot -n 1` again.
  This has no apparent effect, but the step is only sent to the worker process after the director
  is informed that the file `limerick.txt` is static.

- Comment out the `static()` function call and run `stepup boot -n 1` again.
  StepUp will refuse to execute the line numbering step and will show a warning explaining why.
