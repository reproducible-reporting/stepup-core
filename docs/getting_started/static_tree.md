# Static Tree

In the previous tutorial, static files were declared explicitly with the `static()` function.
Explicitly declaring each static file separately is tedious when dealing with many such files.
To simplify matters, StepUp provides several methods to declare static files in bulk.

To simplify this process, StepUp allows declaring an entire directory as static.
You can achieve this simply by passing the directory path to the `static()` function.
In this tutorial, we will see how to use this method to declare static files.

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
stepup build -j 1
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
