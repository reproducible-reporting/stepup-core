# Static Tree

In the previous tutorial, static files were declared explicitly with the `static()` function.
Declaring each file separately becomes tedious when there are many of them.
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

## Try the Following

- Add a file `data/egg.txt` and run StepUp again with the same arguments.
  Inspect the database with `stepup browse` and notice that `data/egg.txt` is completely ignored
  because it was never used as an input.

- Try to copy `data/somefile.txt` to `data/otherfile.txt` with the `copy()` function
  and run StepUp again.
  You will notice that this fails because `data/` is only allowed to contain static files,
  not outputs of steps.
