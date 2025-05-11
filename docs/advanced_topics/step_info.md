# StepInfo Objects

!!! note

    This feature was introduced in StepUp 1.3.0.

The [`step()`][stepup.core.api.step] function always return
an instance of [`StepInfo`][stepup.core.api.StepInfo].
This object holds arguments used to define the step,
which may be useful for creating follow-up steps.
All API functions that create a step by calling the [`step()`][stepup.core.api.step] function
also return the [`StepInfo`][stepup.core.api.StepInfo] object.

Especially for higher-level API functions that create more advanced steps,
such as the ones in [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/),
it can be convenient to access the paths constructed inside the API function
instead of reconstructing them manually in your `plan.py` script.

The `StepInfo` object has `inp`, `env`, `out` and `vol` attributes,
corresponding to the arguments passed into the `step()` function.
The main difference to the arguments passed in,
is that environment variables are substituted in the paths.

In addition, `StepInfo` has three methods: `filter_inp()`, `filter_out()` and `filter_vol()`,
which can be used to get a subset of paths.
These functions take the same arguments as those of the [`glob()`][stepup.core.api.glob] function
and also return an [`NGlobMulti`][stepup.core.nglob] instance.

Note that the `StepInfo` object will only contain information known at the time the step is defined.
Amended information (inputs, outputs, ...) cannot be retrieved from `StepInfo` objects.
Also note that relative paths in `inp`, `out` and `vol` are relative to the working directory.

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
glob("source/**", _defer=True)
info = create_fancy_pdf("source/")

# Copy files related to the fancy PDF, e.g. to publish them or back up files.
mkdir("../public")
copy(info.filter_out("*.pdf").single, "../public/")
mkdir("../backup")
for path_inp in info.inp:
    copy(path_inp, "../backup")
```
