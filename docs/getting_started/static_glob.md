# Static Glob

Explicitly declaring static files with the `static` function from the previous tutorial becomes tedious when dealing with many static files.
To simplify matters, StepUp supports ["glob"](https://en.wikipedia.org/wiki/Glob_(programming)) patterns, i.e., wildcards such as `*` and `?`.

The [`glob()`][stepup.core.api.glob] function is similar to [`static()`][stepup.core.api.static] and supports globbing, including some non-standard glob techniques discussed in the following tutorials.

Here, only the basic usage of [`glob()`][stepup.core.api.glob] is covered.
In the [following tutorial](static_glob_conditional.md), the use of `glob` in conditionals is discussed.
See [Static Named Glob](../advanced_topics/static_named_glob.md) and
[Static Deferred Glob](../advanced_topics/static_deferred_glob.md) for more advanced use cases.


## Example

Example source files: [getting_started/static_glob/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/static_glob)

Create a subdirectory `src/` with two files: `sub/foo.txt` and `sub/bar.txt`.
Also, create a `plan.py` file with the following contents:

```python
{% include 'getting_started/static_glob/plan.py' %}
```

Make the plan executable and run it non-interactively:

```bash
chmod +x plan.py
stepup -n -w1
```

This should produce the following output:

```
{% include 'getting_started/static_glob/stdout.txt' %}
```

Note that all files found by the `glob` function are declared static in the workflow.
Hence, they cannot be outputs of other steps.
(This is not optional.)


## Try the Following

- Run StepUp again without making any changes.
  You will notice that the `./plan.py` step is executed again despite not having changed it.
  When StepUp starts from scratch, it has to assume that new files could have been added (since the last run) that match the glob pattern.
  Hence, a step calling the `glob` function cannot be skipped.
  (This can be avoided when using StepUp interactively. More on that later.)

- Add a file `src/egg.txt` and run StepUp again with the same arguments.
  You will notice that known steps for `sub/foo.txt` and `sub/bar.txt` are skipped.
  A new step is added for `src/egg.txt`.
