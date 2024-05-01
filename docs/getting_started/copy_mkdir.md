# Copy and mkdir

Copying a file or making a directory can be planned using the [`copy()`][stepup.core.api.copy]
and [`mkdir()`][stepup.core.api.mkdir] functions, respectively.
These functions perform a few sanity checks and then create a step with the corresponding shell command.

The example below will also be used in the [Automatic Cleaning](automatic_cleaning.md) tutorial.


## Example

Example source files: [getting_started/copy_mkdir/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/copy_mkdir)

Create a file `plan.py` with the following contents:

```python
{% include 'getting_started/copy_mkdir/plan.py' %}
```

Make it executable and run it with StepUp as follows:

```bash
chmod +x plan.py
stepup -n -w1
```
You should get the following output:

```
{% include 'getting_started/copy_mkdir/stdout.txt' %}
```


## Notes

- StepUp expects all directories to end with a trailing delimiter (`/`).
  In the example above, `mkdir` and `copy` have a `sub/` argument, which is a directory.
  This imposes some clarity on the `plan.py` file and improves readability.

- The second argument of `copy` can also be a file.
  For example, replace `sub/` with `sub/hello.txt`, and you will get exactly the same result.
  You can also use a different filename, such as `sub/hi.txt`.
