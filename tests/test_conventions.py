# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for coding conventions that ruff cannot express.

See the `__all__` section in `CLAUDE.md` for the convention these tests enforce.
"""

import ast
import re

import pytest
from path import Path

from stepup.core.enums import ReturnCode

PACKAGE = Path(__file__).parent.parent / "stepup" / "core"
MODULE_PATHS = sorted(PACKAGE.glob("*.py"))
MODULE_IDS = [path.name for path in MODULE_PATHS]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=path)


def _get_dunder_all(tree: ast.Module) -> tuple[str, ...] | None:
    """Return the `__all__` tuple of a parsed module, or `None` when it is not declared."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            if not isinstance(node.value, ast.Tuple):
                raise TypeError("__all__ must be a tuple")
            return tuple(element.value for element in node.value.elts)
    return None


DUNDER_ALLS = {path.stem: _get_dunder_all(_parse(path)) for path in MODULE_PATHS}


@pytest.mark.parametrize("path", MODULE_PATHS, ids=MODULE_IDS)
def test_dunder_all_declared(path: Path):
    """Every module declares `__all__`, even when it exports nothing."""
    assert DUNDER_ALLS[path.stem] is not None, f"{path.name} does not declare __all__"


@pytest.mark.parametrize("path", MODULE_PATHS, ids=MODULE_IDS)
def test_dunder_all_defined_here(path: Path):
    """Names in `__all__` are defined in the module itself, not re-exported from another one."""
    tree = _parse(path)
    imported = set()
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    for name in DUNDER_ALLS[path.stem] or ():
        assert name in defined or name not in imported, (
            f"{path.name} re-exports {name}: import it from the module that defines it instead"
        )


@pytest.mark.parametrize("path", MODULE_PATHS, ids=MODULE_IDS)
def test_imports_are_exported(path: Path):
    """A name imported from a sibling module must appear in that module's `__all__`.

    There is no exemption for underscore-prefixed names:
    reaching into another module's internals is exactly what this check is meant to prevent.
    Move the name to a module both parties may depend on,
    or promote it to part of the defining module's contract.
    """
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1 and node.module in DUNDER_ALLS:
            module = node.module
        elif node.module is not None and node.module.startswith("stepup.core."):
            module = node.module.removeprefix("stepup.core.")
        else:
            continue
        exported = DUNDER_ALLS[module]
        assert exported is not None, f"{path.name} imports from {module}, which has no __all__"
        for alias in node.names:
            assert alias.name in exported, (
                f"{path.name} imports {alias.name} from {module}, which does not list it in __all__"
            )


def test_example_return_code_constants():
    """The `RETURN_CODE_*` constants in `example.rc` match the `ReturnCode` enum.

    The integration examples assert exit codes through these shell constants,
    so a renumbered or renamed flag must not silently leave them behind:
    the examples would keep passing while testing the wrong bit.
    """
    text = (Path(__file__).parent / "examples" / "example.rc").read_text()
    constants = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^RETURN_CODE_(\w+)=(\d+)$", text, re.MULTILINE)
    }
    assert constants == {flag.name: flag.value for flag in ReturnCode}
