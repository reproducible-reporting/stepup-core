# Cyclic dependencies

Cyclic dependencies are defined in StepUp as closed loops in the dependency graph.
Formally, such a loop is defined as a set of edges that can be followed from supplier to consumer,
such that one arrives back at the starting point.
If you construct cyclic dependencies in a `plan.py`, an error message is generated.

In theory, one could also have cycles in the provenance graph,
but it is not possible to create such loops with the functions in
[stepup.core.api](../reference/stepup.core.api.md).

## Example

Create the following `plan.py`, which is StepUp's equivalent of a snake biting its own tail:

```python
{% include 'advanced_topics/cyclic_dependencies/plan.py' %}
```

Make the plan executable and give it a try as follows:

```bash
chmod +x plan.py
stepup boot -n 1
```

You will get the following terminal output showing that this plan won't work.

```text
{% include 'advanced_topics/cyclic_dependencies/stdout.txt' %}
```
