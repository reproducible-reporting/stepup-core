# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""StepUp instances of cattrs converters and related utilities."""

import cattrs.preconf.json
import cattrs.preconf.pyyaml
from path import Path

__all__ = ("json_converter", "yaml_converter")


def _register_path_hooks(converter):
    """Register structure and unstructure hooks for `path.Path` on a converter.

    `path.Path` is a `str` subclass, so it is (un)structured as a plain string,
    preserving any leading `./` or trailing `/` affixes.
    """
    converter.register_unstructure_hook(Path, str)
    converter.register_structure_hook(Path, lambda value, _: Path(value))


json_converter = cattrs.preconf.json.make_converter()
yaml_converter = cattrs.preconf.pyyaml.make_converter()

_register_path_hooks(json_converter)
_register_path_hooks(yaml_converter)
