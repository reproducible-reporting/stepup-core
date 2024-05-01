# Blocked Steps

As discussed in the [previous tutorial](optional_steps.md),
StepUp has several mechanisms to ignore certain steps.
As a rule, StepUp will always try to execute all steps, and not doing so is the exception.

A valid reason for ignoring some steps is illustrated in the following schematic:

```
     File           In development            File                 Costly
|-------------|      |----------|      |-----------------|      |----------|
|  input.txt  |  =>  |  Step 1  |  =>  |  converted.txt  |  =>  |  Step 2  |
|-------------|      |----------|      |-----------------|      |----------|
```

Imagine that `Step 2` is very expensive and you are developing a script for `Step 1`.
In practice, it takes several iterations to get `Step 1` working properly.
This can be verified by analyzing the file `converted.txt` or with unit tests.

To avoid executing `Step 2` at every iteration in the development of `Step 1`,
you can **block** this step.
All step-creating functions accept an optional `block=True` keyword argument to prevent them from being executed.
Blocking steps is a temporary measure, meant to be reverted once you're done with `Step 1`.

Blocking steps has some consequences:

- Blocked steps remain in the PENDING state, meaning that outdated output files are not cleaned up automatically.
- At the end of the *run phase*, a list of blocked steps is shown, to remind the user that some steps are blocked.
- Subsequent steps, which use outputs of blocked or pending steps, also remain pending.


## Example

Example source files: [advanced_topics/blocked_steps/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/blocked_steps)

The following `plan.py` illustrates the blocking mechanism.
The copy commands are too simple and cheap to justify blocking,
so this is just an example to illustrate the mechanism only.

```python
{% include 'advanced_topics/blocked_steps/plan.py' %}
```

Make this plan executable and run it with StepUp:

```bash
chmod +x plan.py
stepup -n -w1
```

You should get the following terminal output:

```
{% include 'advanced_topics/blocked_steps/stdout.txt' %}
```


## Try the Following

- Unblock the copy step, run StepUp, block it again, and run StepUp again.
  Although the copy commands are no longer executed, their outputs (`b.txt` and `c.txt`)
  are not cleaned up.
  This is the expected behavior because automatic cleaning is only performed when all
  (non-optional) steps have been executed successfully.

- Unblock the copy step, run StepUp, and then make the last copy command optional.
  In this case, the output of the optional step (`c.txt`) will be removed.
