# Rendering Files with Jinja

[Jinja](https://jinja.palletsprojects.com/en/stable/) is a popular templating engine for Python.
Given a template file and a set of variables, Jinja renders the template, i.e. it inserts the variables.
It allows you to create dynamic content by embedding Python-like expressions in your templates.

This section will show you how to use [`render_jinja()`][stepup.core.api.render_jinja].
Internally, it uses StepUp's [`loadns()`][stepup.core.api.loadns] function to load variables,
then uses Jinja2 to render a template, and finally writes the result to an output file.

## Example

Example source files: [`docs/advanced_topics/render_jinja/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/render_jinja)

Create a file `plan.py` with the following contents:

```python
{% include 'advanced_topics/render_jinja/plan.py' %}
```

Prepare a template file `email_template.txt` with the following contents:

```jinja
{% include 'advanced_topics/render_jinja/email_include.txt' %}
```

Finally, create the `config.yaml` configuration file with the following contents:

```yaml
{% include 'advanced_topics/render_jinja/config.yaml' %}
```

Make the plan executable and run StepUp:

```bash
chmod +x plan.py
stepup boot -n 1
```

You should see the following output:

```text
{% include 'advanced_topics/render_jinja/stdout.txt' %}
```

The result is a rendered email template with the values from the configuration file,
written to `email.txt`:

```text
{% include 'advanced_topics/render_jinja/email.txt' %}
```

## Supported Delimiters

With the `mode` option, [`render_jinja()`][stepup.core.api.render_jinja]
supports the following delimiters for Jinja templates:

{% raw %}

- The default Jinja delimiters (`mode="plain"`):
    - `{{ variable }}`
    - `{% statement %}`
    - `{# comment #}`
- The LaTeX-friendly delimiters (`mode="latex"`):
    - `<< variable >>`
    - `<% statement %>`
    - `<# comment #>`

{% endraw %}

The default is `mode="auto"`, which sets the delimiters automatically based on the file extension.

## Try the Following

- Change the values in `config.yaml` and run the plan again.
  The email template should be rendered with the new values.

- You can also separate the loading of variables and the rendering of the template.
  The [`render_jinja()`][stepup.core.api.render_jinja] also accepts a dictionary of variables
  instead of (or in addition to) filenames with variables.
  Use this to load the variables with [`loadns()`][stepup.core.api.loadns] first,
  then pass the loaded variables to [`render_jinja()`][stepup.core.api.render_jinja].
