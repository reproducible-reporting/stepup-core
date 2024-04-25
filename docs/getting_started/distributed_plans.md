# Distributed plans

When your project grows, defining the whole workflow in a single `plan.py` may become inconvenient.
Especially working with nested directories for different parts of the project,
it may be convenient to distribute the workflow over multiple files.


## Example

Create a simple example with a top-level `plan.py` as follows:

```python
{% include 'getting_started/distributed_plans/plan.py' %}
```

The top-level plan defines a few static files and then calls another plan in `sub/`.
Create a files `sub/plan.py` as follows:

```python
{% include 'getting_started/distributed_plans/sub/plan.py' %}
```

Also create two files `part1.txt` and `sub/part2.txt` with a bit of text.
Make both plans executable and run StepUp as follows:

```bash
chmod +x plan.py sub/plan.py
```

You will get the following output:

```
{% include 'getting_started/distributed_plans/stdout.txt' %}
```


## Practical considerations

- The main benefit of multiple plan.py files is to improve the logical structure of your project.
  It may also be helpful when a part of your `plan.py` is computationally demanding, in which
  case it can be factored out such that it does not slow down the rest of the build.
  However, ideally, the `plan.py` scripts execute quickly, leaving the hard work to other steps.
- When there are multiple `plan.py`, keep in mind that their order of execution cannot be relied upon.
  In general, they are executed in parallel.
