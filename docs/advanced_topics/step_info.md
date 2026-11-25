---
description: >-
  Reuse the paths that an API function constructed for a step,
  through the StepInfo object it returns, instead of rebuilding them in plan.py.
---

# `StepInfo` Objects

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

The [`step()`][stepup.core.api.step] function always returns
an instance of [`StepInfo`][stepup.core.stepinfo.StepInfo].
This object holds arguments used to define the step,
which may be useful for creating follow-up steps.
All API functions that create a step by calling the [`step()`][stepup.core.api.step] function
also return the [`StepInfo`][stepup.core.stepinfo.StepInfo] object.

Especially for higher-level API functions that create more advanced steps,
such as the ones in [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/),
it can be convenient to access the paths constructed inside the API function
instead of reconstructing them manually in your `plan.py` script.

The `StepInfo` object has `inp`, `env`, `out` and `vol` attributes,
corresponding to the arguments passed into the `step()` function.
The main difference to the arguments passed in
is that environment variables are substituted in the paths.

In addition, `StepInfo` has three methods: `filter_inp()`, `filter_out()` and `filter_vol()`,
which can be used to get a subset of paths.
These functions take the same arguments as those of the [`glob()`][stepup.core.api.glob] function
and also return a [`NamedGlob`][stepup.core.nglob] instance.

Relative paths in `inp`, `out` and `vol` are relative to `info.workdir`,
which is itself relative to the current directory, unless it is absolute.
Hence, `info.workdir / path` is the path of a dependency relative to the current directory.
To get paths relative to another directory, use
[`rebase()`][stepup.core.stepinfo.StepInfo.rebase],
e.g. `info.rebase(".")` for paths relative to the current directory.

A `StepInfo` only contains the dependencies known when the step was defined.
Dependencies amended while the step runs can be collected with
[`track_amend()`][stepup.core.api.track_amend]
or [`get_amended_info()`][stepup.core.api.get_amended_info],
and combined with [`merge()`][stepup.core.stepinfo.StepInfo.merge],
e.g. `get_info().merge(get_amended_info())`
for the declared and amended dependencies of the current step.

The functions [`dump_step_info()`][stepup.core.stepinfo.dump_step_info]
and [`load_step_info()`][stepup.core.stepinfo.load_step_info]
save and load `StepInfo` objects in JSON files,
which can be loaded from any current directory, also after the directory tree was moved.

## Step Info Files

A step info file is a JSON file written by
[`dump_step_info()`][stepup.core.stepinfo.dump_step_info]
and read by [`load_step_info()`][stepup.core.stepinfo.load_step_info].
Other tools may write such files too, in any language, by following the rules below.
The following file `reports/info.json` describes a step that runs in the directory `analysis/`:

```json
{
  "command": "python3 analyze.py",
  "inp": [
    "../data/raw.csv",
    "analyze.py"
  ],
  "env": [
    "SEED"
  ],
  "out": [
    "results.json"
  ],
  "vol": [
    "analyze.log"
  ],
  "workdir": "../analysis"
}
```

The file contains a single JSON object, as above, or a JSON array of such objects.
Each object has the following keys, all of which are optional:

- `command` is the command executed by the step, as a string.
  It is omitted when the dependencies do not belong to a single step,
  e.g. when they were collected with [`track_amend()`][stepup.core.api.track_amend].
- `workdir` is the working directory of the step.
  A relative `workdir` is relative to the directory that contains the file,
  not to the current directory of the process that reads it.
  When `workdir` is missing, it is the directory that contains the file.
  (An absolute `workdir` is not affected by the location of the file.)
- `inp` is an array of input paths of the step.
- `env` is an array of names of environment variables to which the step is sensitive.
- `out` is an array of output paths of the step.
- `vol` is an array of volatile output paths of the step,
  i.e. side-effect files that StepUp cleans up but does not hash,
  see [Volatile Outputs](volatile_outputs.md).

A missing array is empty.
Relative paths in `inp`, `out` and `vol` are relative to `workdir`.
In the example, the input `../data/raw.csv` is the file `data/raw.csv`
next to the directory `reports/`,
because `workdir` points to `analysis/` next to `reports/`.
Paths use `/` as separator on every platform, and the path of a directory ends with `/`.
The order of the paths and names is irrelevant, and duplicates are removed when the file is read.

Because all relative paths are anchored to the location of the step info file,
it can be read from any current directory,
and it remains valid when the directory tree that contains it is moved as a whole.

The keys `abs_workdir` and `is_abs` are refused.
They appear in the JSON of `json_converter` in `stepup.core.cattrs`,
which holds the working directory as an absolute path.
This is meant for the exchange of StepInfo instances between processes,
who may have a different working directory.

## Example

Let's assume you are using a library with an API function `create_fancy_pdf`,
which takes a source directory as input and outputs a PDF file.
(This is a hypothetical example for illustrative purposes.)
You can find the full list of inputs and the output as follows:

```python
from stepup.core.api import static, copy
from stepup.fancy.api import create_fancy_pdf  # hypothetical API function

# Plan the creation of the fancy PDF
static("source/")
info = create_fancy_pdf("source/")

# Copy files related to the fancy PDF, e.g. to publish them or back up files.
copy(info.filter_out("*.pdf").single, "../public/")
for path_inp in info.inp:
    copy(path_inp, "../backup")
```
