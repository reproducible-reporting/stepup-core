# Cyclic dependencies

Cyclic dependencies are defined in StepUp as closed loops in the "supplier ➜ consumer" graph.
One may construct these in a `plan.py`, which will result in an error message.

In theory, one could also have cycles in the "creator ➜ product" graph, but these are excluded by construction and therefore not relevant to discuss in the tutorial.


## Example

Create the following `plan.py`, which is StepUp's equivalent of a snake biting its own tail:

```python
{% include 'advanced_topics/cyclic_dependencies/plan.py' %}
```

Make the plan executable and give it a try as follows:

```bash
chmod +x plan.py
stepup -n -w1
```

You will get the following terminal output showing that this plan won't work.

```
{% include 'advanced_topics/cyclic_dependencies/stdout.txt' %}
```
