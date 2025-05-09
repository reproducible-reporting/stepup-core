# Load Settings From Configuration Files

It is often useful to load settings from configuration files in different parts of the workflow.
The [`loadns()`][stepup.core.api.loadns] (short for "load namespace") makes this easy:

- It supports loading from JSON, YAML, TOML and Python files.
- It assigns all loaded variables to a namespace, which is easier to use than a dictionary.
- It can load from multiple files, merging them into a single namespace.
- Loaded files are automatically amended as input of the current step,
  unless this is disabled.

## Example

Example source files: [`docs/getting_started/load_namespace/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/load_namespace)

This is just a simple example with a single configuration file and a single script using it.
In a real-world scenario, you would typically have multiple configuration files and scripts.

Create a file `plan.py` with the following contents:

```python
{% include 'getting_started/load_namespace/plan.py' %}
```

It calls a script `print_sentence.py` that loads a configuration file `config.toml` and
writes a sentence to the standard output.

Create the script `print_sentence.py` with the following contents:

```python
{% include 'getting_started/load_namespace/print_sentence.py' %}
```

Finally, create the configuration file `config.toml` with the following contents:

```toml
{% include 'getting_started/load_namespace/config.toml' %}
```

Make the necessary files executable and run the plan:

```bash
chmod +x plan.py print_sentence.py
stepup boot -n 1
```

You should see the following output:

```text
{% include 'getting_started/load_namespace/stdout.txt' %}
```

As you can see, the sentence contains elements from the configuration file.

## Try the Following

- Run the plan again with `stepup boot -n 1`.
  The steps are not executed again, as expected.

- Change some fields in the configuration file and run the plan again.
  This time, the step `print_sentence.py` is executed again,
  now with the new values from the configuration file.
