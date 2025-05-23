# Dependencies

This tutorial demonstrates how StepUp tracks dependencies.

## Example

Example source files: [`docs/getting_started/dependencies/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/dependencies)

The following `plan.py` defines two steps, with the second making use of the output from the first.

```python
{% include 'getting_started/dependencies/plan.py' %}
```

The placeholders `${inp}` and `${out}` are replaced by the `inp` and `out` keyword arguments.
(This happens early, before the steps are sent to the director process.)

The [`graph()`][stepup.core.api.graph] function writes the graph in a few formats,
which are used for visualization below.

Now run StepUp with two workers:

```bash
stepup boot -n 2
```

You will see the following output:

```text
{% include 'getting_started/dependencies/stdout.txt' %}
```

Despite the fact that StepUp has launched two workers, it carries out your `runsh` steps sequentially,
because it knows that the output of the first step will be used by the second.

Note, however, that the `echo` commands are already started before `./plan.py` has finished.
This is the expected behavior: even without a complete overview of all the build steps,
StepUp will start the steps for which it has sufficient information.

## Graphs

The `plan.py` script writes a few files to analyze and visualize the graphs StepUp uses internally.
The file `graph.txt` is a detailed human-readable version of `.stepup/graph.db`:

```text
{% include 'getting_started/dependencies/graph.txt' %}
```

This text format may not always be the most convenient way
to understand how StepUp connects all the steps and files.
A more intuitive picture can be created with [GraphViz](https://graphviz.org/)
using the `.dot` files as input.
The figures below were created using the following commands:

```bash
dot -v graph_provenance.dot -Tsvg -o graph_provenance.svg
dot -v graph_dependency.dot -Tsvg -o graph_dependency.svg
```

The workflow in StepUp consists of two graphs involving (a subset of) the same set of nodes:
the **supplier graph** and the **creator graph**.

### Dependency Graph

This graph shows how information is passed from one node to the next as the steps are executed.

![graph_dependency.svg](dependencies/graph_dependency.svg)

This is an intuitive graph showing the execution flow.
A similar graph is used by most other build tools.
Not shown in this diagram are the directories, which StepUp treats in the same way as files.

### Provenance Graph

This one shows who created each node in the graph:

![graph_provenance.svg](dependencies/graph_provenance.svg)

This diagram is a little less intuitive and requires more explanation.
Each node in StepUp's workflow is created by exactly one other node,
except for the Root node, which is its own creator.
In this example, there are three nodes that create other nodes:

- The `root` node is an internal node controlled by StepUp.
  Upon startup, StepUp creates `root` and a few other nodes by default:
    - The initial `plan.py` file
    - The initial `runsh ./plan.py` step (with working directory `./`).
    - The working directory `./` is created just like any other directory that is used.

- The `runsh ./plan.py` step creates two nodes,
  see the two `runsh()` function calls in the `plan.py` script above.
    - The `runsh grep ...` step.
    - The `runsh echo ...` step.

- The `runsh echo ...` step creates one output file: `story.txt`.

This provenance graph is used by StepUp to decide which steps to keep and which to clean up.
After some files have changed and StepUp is run again, some nodes may no longer be created.
These "old" nodes will still exist in the database as "orphaned" nodes, i.e. without a creator.
After all steps have been successfully completed,
StepUp will remove orphaned nodes that are not suppliers to other steps.
When output file nodes are deleted, the corresponding files are also removed from disk.
(This is done carefully: StepUp will only remove files
if it knows they were created in a previous run and
if the file hash still matches the one recorded immediately after the file was built.)
If some steps use orphaned nodes as input, those steps will remain pending,
resulting in an incomplete build and blocking the removal of the orphaned nodes.

Example:

- After modifying `plan.py` and rerunning this step,
  all nodes created by the `./plan.py` step will be orphaned.
- The new `plan.py` may recreate some of the old nodes in exactly the same way,
  in which case the orphaned nodes will simply be restored,
  along with all of their products and related information.
- If some nodes are not recreated, they will remain orphaned,
  and will be removed after a complete and successful build.
- The new `plan.py` can also define new nodes, which simply extend the graph.
- Nodes that are recreated with different properties will override any existing orphaned nodes.

## Try the Following

- Run `stepup boot -n 2` again. As expected, the steps are now skipped.

- Modify the `grep` command to select the second line and run `stepup boot -n 2` again.
  The `echo` commands are skipped as they have not changed.

- Change the order of the two steps in `plan.py` and run `stepup boot -n 2`.
  The step `./plan.py` is executed because the file has changed,
  but the `echo` and `grep` steps are skipped.
  This shows that `plan.py` is nothing but a plan, and it does not execute the steps itself.
  When `plan.py` is executed, it simply sends instructions to the director process.

- Rename the file `story.txt` to `lines.txt` (in both steps) and restart StepUp.
  The old `story.txt` output file will be automatically removed from disk,
  since this is an (intermediate) output file whose node will be orphaned and cleaned up.
