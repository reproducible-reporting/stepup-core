# Dependencies

This tutorial shows how StepUp keeps track of dependencies.


## Example

Example source files: [getting_started/dependencies/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/dependencies)

The following `plan.py` defines two steps, the second making use of the output from the first.

```python
{% include 'getting_started/dependencies/plan.py' %}
```

The placeholders `${inp}` and `${out}` are replaced by the `inp` and `out` keyword arguments.
(This happens early, before the steps are sent to the director process.)

Now run StepUp with two workers:

```bash
stepup -n -w2
```

You will see the following output:

```
{% include 'getting_started/dependencies/stdout.txt' %}
```

Despite the fact that StepUp launched two workers, it carries out the steps sequentially,
because it knows that the output of the first step is used by the second.

Note, however, that the `echo` commands are already started before `./plan.py` has completed.
This is the expected behavior: even without a complete overview of all build steps,
StepUp will start steps for which it has sufficient information.

## Try the following

- Just run `stepup -n -w2` again. As expected, the steps are now skipped.
- Modify the `grep` command to select the second line and run `stepup -n -w2` again.
  The `echo` commands are skipped, since they have not changed.
- Change the order of the two steps in `plan.py` and run `stepup -n -w2`.
  The step `./plan.py` is executed because the file changed,
  but the `echo` and `grep` steps are skipped.
  This illustrates that `plan.py` is nothing but a plan being communicated to the director process.
  It does not execute the steps.
