# Static deferred glob

When working with large datasets (many thousands of files),
it is not desirable to make all these files static when only a few of them are used.
This can be solved with StepUp's *deferred glob*,
which will only declare static files matching a glob pattern when they are used as inputs.


## Example

Example source files: [advanced_topics/static_deferred_glob/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/static_deferred_glob)

Create two text files with some content: `foo.txt` and `bar.txt`,
and alsos the following `plan.py`:

```python
{% include 'advanced_topics/static_deferred_glob/plan.py' %}
```

Run the plan interactively with StepUp:

```bash
chmod +x plan.py
stepup -n -w1
```

You should have the following screen output:

```
{% include 'advanced_topics/static_deferred_glob/stdout.txt' %}
```

As expected, `foo.txt` is used as a static file,
but this would also have been the case without the `_defer=True` option.
The key difference is that, internally, StepUp does not build a list of all matching `*.txt` files.
This can be seen when inspecting the file `graph.txt`, which has no trace of `bar.txt`:

```
{% include 'advanced_topics/static_deferred_glob/graph.txt' %}
```

The node `dg:'*.txt;` in the graph is the result of adding the `_defer=True` option.
This node will create static files as they are needed by other steps.
This option is ideal when there are a large number of files that could match the pattern,
of which most are irrelevant for the build.
In this example, there could be thousands of `.txt` files and
this would not have any effect on the resources consumed by StepUp.


## Try the following

- When using deferred globs, steps cannot create outputs that match the deferred glob.
  It would mean that a built file must be made static, which is obviously inconsistent.
  Cause this error by adding a step `copy("foo.txt", "foo2.txt")`.
- Remove the `_defer=True` option and inspect the corresponding `graph.txt`
  to observe that now `bar.txt` is indeed included in the graph.
