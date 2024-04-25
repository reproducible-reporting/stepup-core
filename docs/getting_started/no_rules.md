# No rules

Most other built tools introduce the concept of a *build rule*,
to specify how a common build step can be applied to different inputs.
StepUp does not need to introduce build rules because Python functions and loops fulfill their role.

StepUp already comes with a few built-in rules defined this way:
[`plan()`][stepup.core.api.plan],
[`copy()`][stepup.core.api.copy],
[`mkdir()`][stepup.core.api.mkdir],
[`getenv()`][stepup.core.api.getenv] and
[`script()`][stepup.core.api.script].
Some of these were already discussed in the previous tutorials,
and their source code offers some inspiration for writing your own.


## Example

Here we show a simple example of a custom rule to convert a text file to upper case with the `tr` command.

Create the following `plan.py`:

```python
{% include 'getting_started/no_rules/plan.py' %}
```

In addition, make two text files `lower1.txt` and `lower2.txt` with some random contents.
Then make the plan executable and launch StepUp:

```bash
chmod +x plan.py
stepup -n -w1
```

This will show the following output:

```
{% include 'getting_started/no_rules/stdout.txt' %}
```
