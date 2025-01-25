# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""Deferred patterns for static files."""

from typing import TYPE_CHECKING

import attrs

from .cascade import Node

if TYPE_CHECKING:
    pass

__all__ = ("DeferredGlob",)


@attrs.define
class DeferredGlob(Node):
    """A pattern to make files static when they are used as input."""

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return "dg"

    def add_supplier(self, supplier: Node) -> int:
        raise NotImplementedError("A deferred glob does not use suppliers.")

    def detach(self):
        """Clean up an orphaned node because it loses a product node.

        Completely remove this deferred glob, making reuse impossible.
        """
        for product in self.products():
            product.orphan()
        self.orphan()
        self.clean()
        self.con.execute("DELETE FROM node WHERE i = ?", (self.i,))
