<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Snipwise Configuration

Consult the full documentation at <https://reproducible-reporting.github.io/snipwise/>.

```toml
# The Markdown files show the abstract as it is written below, links and all.
[[targets]]
patterns = ["README.md", "docs/index.md"]

# The summary of the Python package metadata, which is a single-line TOML string.
[[targets]]
patterns = ["pyproject.toml"]
scanner = "regex"
regex = '(?m)^description = "(?P<content>[^"]*)"$'
snippets = ["tagline"]
render = "{{ content | unwrap }}"

# The meta description that every documentation page carries.
[[targets]]
patterns = ["mkdocs.yaml"]
scanner = "regex"
regex = '(?m)^site_description: (?P<content>.*)$'
snippets = ["tagline"]
render = "{{ content | unwrap }}"

# The docstring of the package, which is a single-line string.
[[targets]]
patterns = ["stepup/core/__init__.py"]
scanner = "regex"
regex = '(?m)^"""(?P<content>[^"]*)"""$'
snippets = ["tagline"]
render = "{{ content | unwrap }}"

# The keywords array of the Python package metadata.
[[targets]]
patterns = ["pyproject.toml"]
snippets = ["keywords"]
render = '''{{ content | prefix('"') | suffix('",') }}'''

# The abstract of the citation metadata, which is a folded YAML block scalar.
# The template terminates the last line, because the region includes its newline.
[[targets]]
patterns = ["CITATION.cff"]
scanner = "regex"
regex = '(?m)^abstract: >-\n(?P<content>(?:^  .*\n)+)'
snippets = ["abstract"]
render = "{{ content | plain | prefix('  ') }}\n"

# The keywords sequence of the citation metadata.
[[targets]]
patterns = ["CITATION.cff"]
snippets = ["keywords"]
render = "{{ content | prefix('- ') }}"

# The Zenodo metadata, which is JSON and therefore carries no markers.
[[targets]]
patterns = [".zenodo.json"]
scanner = "json"
insert = [
  { snippet = "abstract", pointer = "/description", render = "{{ content | plain | unwrap }}" },
  { snippet = "keywords", pointer = "/keywords", shape = "lines" },
]
```

## `tagline`

```text
A dynamic Python build tool for reproducible workflows
```

## `abstract`

```markdown
StepUp is a dynamic build tool and a modern alternative to
[Make](https://en.wikipedia.org/wiki/Make_(software)).
Its defining feature is that workflow generation and execution are unified:
a `plan.py` script defines the initial build steps.
While the workflow is being executed, any step can add more steps and dependencies,
based on the outputs built so far.
This makes StepUp ideal for builds
where the full set of dependencies cannot be determined in advance.
```

## `keywords`

```text
asyncio
build automation
build tool
dynamic dependencies
incremental build
POSIX
Python
reproducibility
reproducible research
StepUp
terminal application
workflow
workflow management
```
