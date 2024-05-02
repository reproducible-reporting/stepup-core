# Amending Static Inputs

Occasionally, it may be convenient to declare a static file and then use it as input in the same script.
As of StepUp 1.2.0, this is allowed and no longer treated as a cyclic dependency.


## Example

Create the following `plan.py`, which declares a static file, amends it as input, and then opens it to print it to the standard output.

```python
{% include 'advanced_topics/amending_static_inputs/plan.py' %}
```

Also create a `config.txt` file with some contents.

In more realistic scenarios, `config.txt` may be used to decide which steps to add etc.
For a more elaborate example, take a look at the [`plan.py`](https://github.com/reproducible-reporting/stepup-core/blob/main/docs/plan.py) that is used to run all tutorial examples.

Make `plan.py` executable and run it as follows:

```bash
chmod +x plan.py
stepup -n -w1
```

You should get the following terminal output:

```
{% include 'advanced_topics/amending_static_inputs/stdout.txt' %}
```
