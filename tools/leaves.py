#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later

import argparse
import ast
from collections import defaultdict

from path import Path


def get_module_name(file_path: Path, root_dir: Path) -> str:
    """Converts a file path to its relative Python module dot-notation."""
    rel_path = file_path.relpath(root_dir)
    if rel_path.name == "__init__.py":
        parts = rel_path.parent.parts()
    else:
        parts = list(rel_path.parts())
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return ".".join(parts) if parts else root_dir.name


class ImportVisitor(ast.NodeVisitor):
    """AST Visitor to collect imported module names."""

    def __init__(self, current_module: str, all_modules: set[str]):
        self.current_module = current_module
        self.all_modules = all_modules
        self.dependencies = set()

    def visit_If(self, node: ast.If):
        """Intercept 'if' nodes and skip TYPE_CHECKING bodies."""
        if is_type_checking_guard(node.test):
            # Skip node.body (type-checking imports)
            # Only visit node.orelse (if there's an `else:` branch)
            for child in node.orelse:
                self.visit(child)
            return

        # Continue normal traversal for non-TYPE_CHECKING 'if' blocks
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self._add_if_internal(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            # Handle relative imports vs absolute imports
            if node.level > 0:
                # Resolving relative import depth
                pkg_parts = self.current_module.split(".")
                base_parts = pkg_parts[: -node.level] if len(pkg_parts) >= node.level else []
                resolved_base = ".".join(base_parts)
                target = f"{resolved_base}.{node.module}" if resolved_base else node.module
            else:
                target = node.module

            self._add_if_internal(target)

            # Also check if imported symbol itself is a module inside the package
            for alias in node.names:
                self._add_if_internal(f"{target}.{alias.name}")

        self.generic_visit(node)

    def _add_if_internal(self, target_mod: str):
        # Match target against known internal modules
        for mod in self.all_modules:
            if mod == self.current_module:
                continue
            if target_mod == mod or target_mod.startswith(mod + "."):
                self.dependencies.add(mod)


def parse_package(pkg_dir: Path):
    """Scans all .py files and constructs the dependency adjacency graph."""
    py_files = list(pkg_dir.glob("*.py"))

    # 1. Collect all valid internal module dot-paths
    all_modules = {get_module_name(f, pkg_dir.parent) for f in py_files}

    # 2. Extract internal dependencies per module
    graph = defaultdict(set)
    for f in py_files:
        mod_name = get_module_name(f, pkg_dir.parent)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            visitor = ImportVisitor(mod_name, all_modules)
            visitor.visit(tree)
            graph[mod_name] = visitor.dependencies
        except (SyntaxError, UnicodeDecodeError):
            # Fallback for unparseable files
            graph[mod_name] = set()

    return graph


def is_type_checking_guard(test_node: ast.expr) -> bool:
    """Checks if an if-condition is checking `TYPE_CHECKING` or `typing.TYPE_CHECKING`."""
    # Matches `if TYPE_CHECKING:`
    if isinstance(test_node, ast.Name) and test_node.id == "TYPE_CHECKING":
        return True

    # Matches `if typing.TYPE_CHECKING:` or `if t.TYPE_CHECKING:`
    return bool(isinstance(test_node, ast.Attribute) and test_node.attr == "TYPE_CHECKING")


def print_leaf_layers(graph: dict[str, set[str]]):
    """Iteratively prints and trims leaf modules (out-degree 0)."""
    layer = 1
    remaining_graph = {k: set(v) for k, v in graph.items()}

    while remaining_graph:
        # Leaves are nodes with zero remaining internal dependencies
        leaves = {mod for mod, deps in remaining_graph.items() if not deps}

        if not leaves:
            print("\n[!] Circular Dependency Detected among remaining modules:")
            for mod, deps in remaining_graph.items():
                print(f"    - {mod} -> depends on {list(deps)}")
            break

        print(f"\nLayer {layer} leaves:")
        for leaf in sorted(leaves):
            print("  ", leaf)

        # Remove leaves from graph
        for leaf in leaves:
            del remaining_graph[leaf]

        # Strip leaves from remaining modules' dependency sets
        for deps in remaining_graph.values():
            deps.difference_update(leaves)

        layer += 1


def main():
    parser = argparse.ArgumentParser(
        description="Analyze package dependencies by iteratively trimming leaf modules."
    )
    parser.add_argument(
        "package_path",
        type=Path,
        help="Path to the directory containing the package source code.",
        nargs="?",
        default=Path("./stepup/core"),
    )
    args = parser.parse_args()

    pkg_path = args.package_path.normpath()
    if not pkg_path.is_dir():
        print(f"Error: Directory '{pkg_path}' does not exist.")
        return

    graph = parse_package(pkg_path)
    print_leaf_layers(graph)


if __name__ == "__main__":
    main()
