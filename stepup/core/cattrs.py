# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""cattrs converters preconfigured for StepUp, and the hooks they register."""

from enum import Enum
from functools import cache

import cattrs.preconf.json

from .nglob import NamedGlob
from .path import Path, is_path_type

__all__ = ("get_yaml_converter", "json_converter")


def _unstructure_str(value: str) -> str:
    """Unstructure an instance of `str` or of a subclass to a plain string.

    A path keeps any leading `./` or trailing `/` affixes,
    and a string enum becomes its value.
    """
    return value.value if isinstance(value, Enum) else str(value)


def _register_path_hooks(converter):
    """Register structure and unstructure hooks for path classes on a converter.

    A path is a `str` subclass, so it is (un)structured as a plain string,
    preserving any leading `./` or trailing `/` affixes.
    The hooks apply to `Path` of StepUp and of the `path` library,
    and structuring creates an instance of the requested class.
    """
    # The unstructure hook is registered for `str`, so that it covers both path classes
    # without importing the `path` library (see `ConventionTests.test_slash_paths`).
    # It then takes precedence over the converter's own hooks for string enums,
    # which is why it handles those as well.
    converter.register_unstructure_hook(str, _unstructure_str)
    converter.register_structure_hook_func(is_path_type, lambda value, cls: cls(value))


def _unstructure_named_glob(ng: NamedGlob) -> dict:
    return {
        "pattern": ng.pattern,
        "subs": ng.subs,
        "results": [
            [list(key), sorted(str(path) for path in paths)]
            for key, paths in sorted(ng.results.items())
        ],
    }


def _structure_named_glob(data: dict, _type: type | None = None) -> NamedGlob:
    return NamedGlob(
        data["pattern"],
        data["subs"],
        {tuple(key): {Path(path) for path in paths} for key, paths in data["results"]},
    )


def _register_nglob_hooks(converter):
    """Register structure and unstructure hooks for `NamedGlob`.

    Only the state that cannot be recomputed is (un)structured explicitly:
    `pattern`, `subs`, and `results`.
    The remaining attributes are `init=False` fields that `attrs` derives automatically
    when the object is reconstructed.
    """
    converter.register_unstructure_hook(NamedGlob, _unstructure_named_glob)
    converter.register_structure_hook(NamedGlob, _structure_named_glob)


json_converter = cattrs.preconf.json.make_converter()

_register_path_hooks(json_converter)
_register_nglob_hooks(json_converter)


@cache
def get_yaml_converter() -> cattrs.Converter:
    """Return the converter for data written to YAML files.

    The converter is created on first use, so that importing this module does not import PyYAML.
    """
    # PyYAML is imported here instead of at the top,
    # because importing it slows down the startup of every plan and step,
    # also when they never read or write YAML.
    # As of November 2026, it took about 10% of the import time of `stepup.core.api`,
    # measured on Linux with Python 3.14 and `python -X importtime`.
    from cattrs.preconf import pyyaml  # noqa: PLC0415

    converter = pyyaml.make_converter()
    _register_path_hooks(converter)
    _register_nglob_hooks(converter)
    return converter
