This example shows how to chain multiple calls with the call protocol.

This is a simple linear chain of two calls.
Re-using the output of one call in multiple calls is a trivial extension of this example,
If you want to use output from calls as input to a follow-up call,
An additional step is needed to merge the JSON outputs, e.g. with the `jq` program:

```python
from stepup.core.step import step

step("jq -s '.[0] * .[1]' ${inp} > ${out}", inp=["script1_out.json", "script2_out.json"], out="merged.json")
```

This example assumes that the two outputs are JSON objects to be merged into a single object.
