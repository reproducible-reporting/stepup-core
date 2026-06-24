# File copy

The [`copy()`][stepup.core.api.copy] function can be used to plan file copying steps.
It automatically performs several sanity checks and generates a corresponding shell command step.

The example below will also be used in the [Automatic Cleaning](automatic_cleaning.md) tutorial.

## Example

Example source files: [`docs/getting_started/copy/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/copy)

Create a file `plan.py` with the following contents:

```python
{% include 'getting_started/copy/plan.py' %}
```

Make it executable and run it with StepUp as follows:

```bash
chmod +x plan.py
stepup build -j 1
```

You should get the following output:

```text
{% include 'getting_started/copy/stdout.txt' %}
```

## Notes

- The second argument is a directory into which the file will be copied.

- StepUp creates subdirectories automatically if they don't exist yet.

- The second argument of `copy()` can also be a file.
  For example, replace `sub/` with `sub/hello.txt`, and the file will be copied to that specific destination.
  You can also use a different filename, such as `sub/hi.txt`.
