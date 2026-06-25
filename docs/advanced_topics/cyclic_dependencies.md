# Cyclic dependencies

Cyclic dependencies are defined in StepUp as closed loops in the dependency graph.
Formally, such a loop is defined as a set of `supplier ➜ consumer` edges
(as introduced under [Edges](../getting_started/introduction.md#edges))
that can be followed such that one arrives back at the starting point.
If you construct cyclic dependencies in a `plan.py`, an error message is generated.

The [stepup.core.api](../reference/stepup.core.api.md) enforces acyclic dependencies
in the provenance graph and cannot be introduced accidentally by the user.

## Example

Create the following `plan.py`, which is StepUp's equivalent of a snake biting its own tail:

```python
{% include 'advanced_topics/cyclic_dependencies/plan.py' %}
```

Make the plan executable and give it a try as follows:

```bash
chmod +x plan.py
sb -j 1
```

You will get the following terminal output showing that this plan won't work.

```text
{% include 'advanced_topics/cyclic_dependencies/stdout.txt' %}
```
