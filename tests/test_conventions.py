# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for coding conventions that ruff cannot express.

See the `__all__` section in `CLAUDE.md` for the convention these tests enforce,
and `stepup/core/CLAUDE.md` for the layering of the subpackages.
"""

import ast

import pytest

from stepup.core.path import Path
from stepup.core.pytest import ConventionTests

PACKAGE_DIR = Path(__file__).parent.parent / "stepup/core"

BAND1 = frozenset(
    [
        "cattrs",
        "constants",
        "enums",
        "exceptions",
        "hash",
        "nglob",
        "path",
        "stepinfo",
        "tracebacks",
        "utils",
    ]
)
"""Top-level modules with the shared vocabulary, which import no subpackage."""

BAND2 = frozenset(["api", "call", "extapi", "render_jinja", "script"])
"""Top-level modules of the step-side API."""

BAND3 = frozenset(["__init__", "__main__", "pytest"])
"""Top-level modules that are entry points, which may import anything."""

ALLOWED_SUBPACKAGE_IMPORTS = {
    "platform": frozenset(),
    "graph": frozenset(),
    "rpc": frozenset(["platform"]),
    "watch": frozenset(["platform"]),
    "launch": frozenset(["graph", "platform"]),
    "cli": frozenset(["graph", "platform", "rpc", "watch"]),
    "director": frozenset(["graph", "launch", "platform", "rpc", "watch"]),
}
"""The subpackages that the modules of each subpackage may import from."""

BAND2_IMPORTS = frozenset(["platform", "rpc"])
"""The subpackages that the step-side API may import from."""


class TestConventions(ConventionTests):
    package = "stepup.core"
    # `path.py` defines the `Path` class on top of the `path` library.
    native_path_modules = ("path.py",)


def _units() -> list[str]:
    """The top-level modules and subpackages of `stepup.core`."""
    return sorted(
        [path.stem for path in PACKAGE_DIR.files("*.py")]
        + [path.name for path in PACKAGE_DIR.dirs() if (path / "__init__.py").is_file()]
    )


def _imported_units(unit: str) -> dict[str, list[str]]:
    """The other units that a unit imports from, each with the places of the imports."""
    unit_dir = PACKAGE_DIR / unit
    paths = sorted(unit_dir.walkfiles("*.py")) if unit_dir.is_dir() else [unit_dir + ".py"]
    result = {}
    for path in paths:
        depth = len(path.relpath(PACKAGE_DIR).splitall()) - 2
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                parts = (node.module or "").split(".")
                if parts[:2] != ["stepup", "core"] or len(parts) < 3:
                    continue
                target = parts[2]
            elif node.level == depth + 1 and node.module is not None:
                target = node.module.split(".")[0]
            else:
                # An import within the unit itself.
                continue
            if target != unit:
                result.setdefault(target, []).append(f"{path.relpath(PACKAGE_DIR)}:{node.lineno}")
    return result


def test_units_are_classified():
    classified = BAND1 | BAND2 | BAND3 | set(ALLOWED_SUBPACKAGE_IMPORTS)
    assert set(_units()) == classified


@pytest.mark.parametrize("unit", _units())
def test_layering(unit: str):
    """Each module imports only from the layers below it, see `stepup/core/CLAUDE.md`."""
    if unit in BAND1:
        allowed = BAND1
    elif unit in BAND2:
        allowed = BAND1 | BAND2 | BAND2_IMPORTS
    elif unit in BAND3:
        return
    elif unit in BAND2_IMPORTS:
        allowed = BAND1 | ALLOWED_SUBPACKAGE_IMPORTS[unit]
    else:
        allowed = BAND1 | BAND2 | ALLOWED_SUBPACKAGE_IMPORTS[unit]
    forbidden = {
        target: places for target, places in _imported_units(unit).items() if target not in allowed
    }
    assert not forbidden, f"{unit} may not import from {forbidden}"
