# Static Named Glob

Conventional glob patterns support a handful of different wildcards.
For advanced use cases, StepUp also supports an in-house extension called "named glob".
For example, the following pattern will only match files with matching strings at the placeholders.

```
prefix_${*name}_something_${*name}.txt
```

The following will match:

```
prefix_aaa_something_aaa.txt
prefix_bbb_something_bbb.txt
```

The following won't:

```
prefix_aaa_something_bbb.txt
prefix_bbb_something_aaa.txt
```

Named globs are often useful when working with files distributed over multiple directories, each having a central file that repeats a part of the directory name.


## Example

Example source files: [advanced_topics/static_named_glob/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/static_named_glob)

In the example below, each directory represents a chapter from course notes, containing source files for individual sections.
In a realistic setting, one could envision building a PDF presentations from LaTeX sources instead.
To keep the example independent of StepUp RepRep, text files will be copied to Markdown files, which will then be concatenated.

Create the following directory layout with markdown files:

```
ch1/
ch1/sec1_1_introduction.txt
ch1/sec1_2_objectives.txt
ch2/
ch2/sec2_1_mathematical_requisites.txt
ch2/sec2_2_theory.txt
ch3/
ch3/sec3_1_applications.txt
ch3/sec3_2_discussion.txt
ch4/sec4_1_summary.txt
```

Create the following `plan.py`:

```python
{% include 'advanced_topics/static_named_glob/plan.py' %}
```

Note that the substrings matching the named glob patterns are accessible as attributes of the `match` object.
For example, `match.ch` is the chapter number (as a string).

Make the plan executable and run StepUp:

```bash
chmod +x plan.py
stepup -n -w1
```

You should get the following output:

```
{% include 'advanced_topics/static_named_glob/stdout.txt' %}
```
