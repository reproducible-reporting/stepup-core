# HERE and ROOT variables

When a worker runs a step, it defines several environment variables, including `HERE` and `ROOT`, which can be relevant for writing advanced scripts.
(The workers also define variables starting with `STEPUP_`, but these are only useful to StepUp itself, not to end users.)

The two variables are defined as follows:

- `HERE` contains the relative path from the directory where StepUp was started to the current working directory of the step.
- `ROOT` contains the opposite: the relative directory from the current working directory to the directory where StepUp was started.

Hence, `HERE/ROOT` and `ROOT/HERE` normalize to the current directory: `./`.

These variables can be useful in the following cases:

- For out-of-source builds, where you want to replicate the directory structure of the source material.
  (See example below.)
- To reference a local script that is stored in the top-level directory of your project: `${ROOT}/script.py`


## Example

Example source files: [advanced_topics/here_and_root/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/here_and_root)

This example represents a minimal out-of-source build, which is nevertheless involving several files, due to the inherent complexity of out-of-source builds.

Create a `source/` directory with the following `source/plan.py`:

```python
{% include 'advanced_topics/here_and_root/source/plan.py' %}
```

Also create a `source/sub/` directory with a file `source/sub/example.txt` (arbitrary contents) and the following `source/sub/plan.py`:

```python
{% include 'advanced_topics/here_and_root/source/sub/plan.py' %}
```

Make the scripts executable and run everything as follows:

```bash
chmod +x plan.py sub/plan.py
stepup -n -w1
```

You should get the following terminal output:

```
{% include 'advanced_topics/here_and_root/stdout.txt' %}
```

The top-level `plan.py` provides some infrastructure: some static files and creating the public directory where the outputs will be created.

The script `sub/plan.py` uses the `ROOT` and `HERE` variables in a way that is independent of the location of this `sub/plan.py`.
It may therefore be fixed in an environment variable, for example:

```bash
export DST='../public/${HERE}'
```

Then you can get this path in any `plan.py` as follows:

```python
from stepup.core.api import getenv
dst = getenv("DST", is_path=True)
```

The `is_path=True` option implies that the variable is a path defined globally.
If it is a relative path, it will be interpreted relative to the working directory where StepUp was started and will be translated to the working directory of the script calling `getenv`.
Any variables present in the environment variable will also be substituted once.


## Try the Following

- Modify the scripts `plan.py` and `sub/plan.py` to utilize a `DST` variable as explained above.
  To achieve this, define `DST` externally, for instance, by starting StepUp as `DST='../public/${HERE}' stepup -n -w1`.

- As a follow-up to the previous point, run StepUp with a different `DST` value.
  For example: `DST='../out/${HERE}' stepup -n -w1`.
  You will see that all old output files get cleaned up after the new output is created.
