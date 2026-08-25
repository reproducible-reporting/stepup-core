# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Workflow` is a `Trellis` subclass with more concrete node implementations."""

import asyncio
import json
import logging
import os
import re
import stat
import textwrap
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping

import attrs
from path import Path

from .cattrs import json_converter
from .constants import PLAN_PY, STEPUP_DIR
from .enums import (
    FILE_ROLE_BY_STATE,
    FILE_STATES_BY_ROLE,
    OBSERVABLE_FILE_STATES,
    TARGET_FORBIDDEN_STATES,
    Availability,
    FileRole,
    FileState,
    HashUpdateCause,
    Need,
    StepState,
)
from .exceptions import ConsistencyError, GraphError
from .file import REGULAR_OUTPUT_WHERE, File
from .hash import FileHash, fmt_short_digest
from .nglob import NamedGlob, glob_base_dir, has_any_wildcards
from .path import dir_range_upper, parent_dir
from .sqlite3 import prefix_clause
from .static_tree import StaticTree
from .step import RESERVED_ENV_VARS, Step
from .trellis import Node, Root, Trellis
from .utils import is_debug

__all__ = ("GlobViolation", "Workflow", "mark_dir_to_be_deleted")


logger = logging.getLogger(__name__)


# Enforce Workflow's creator-kind and dependency-kind rules at the database level,
# as a backstop against a bug that writes directly to the node or dependency tables,
# bypassing the graph-mutation API that is normally responsible for upholding these rules.
# These are the only Workflow-level invariants that don't belong to a single node kind's own
# satellite schema, so they live here instead.
WORKFLOW_SCHEMA = f"""
-- Which kinds may create a node depends on the node's own kind:
-- - root: file, step, root
-- - file: (cannot create nodes)
-- - step: file, step and static_tree
-- - static_tree: file
-- A NULL creator (detached-on-creation) is always allowed and is not covered by these triggers.
-- The root node is exempt (kind = 'root' in the WHEN clause):
-- it is inserted once with creator = 1 (self) directly in SQL, outside the normal creation path.

-- Enforce the creator-kind rules on insert
CREATE TRIGGER IF NOT EXISTS node_check_creator_kind_ins AFTER INSERT ON node
WHEN NEW.creator IS NOT NULL AND NEW.kind != '{Root.kind()}'
BEGIN
    SELECT RAISE(ABORT, 'invalid creator kind for new node')
    FROM node AS c
    WHERE c.i = NEW.creator
        AND NOT (
            (
                NEW.kind = '{File.kind()}'
                AND c.kind IN ('{Step.kind()}', '{StaticTree.kind()}', '{Root.kind()}')
            )
            OR (NEW.kind = '{Step.kind()}' AND c.kind IN ('{Step.kind()}', '{Root.kind()}'))
            OR (NEW.kind = '{StaticTree.kind()}' AND c.kind = '{Step.kind()}')
        );
END;

-- Enforce the creator-kind rules on update,
-- to catch a recycle that changes the kind of an existing node.
CREATE TRIGGER IF NOT EXISTS node_check_creator_kind_upd AFTER UPDATE OF creator ON node
WHEN NEW.creator IS NOT NULL AND NEW.kind != '{Root.kind()}'
BEGIN
    SELECT RAISE(ABORT, 'invalid creator kind after recycle')
    FROM node AS c
    WHERE c.i = NEW.creator
        AND NOT (
            (
                NEW.kind = '{File.kind()}'
                AND c.kind IN ('{Step.kind()}', '{StaticTree.kind()}', '{Root.kind()}')
            )
            OR (NEW.kind = '{Step.kind()}' AND c.kind IN ('{Step.kind()}', '{Root.kind()}'))
            OR (NEW.kind = '{StaticTree.kind()}' AND c.kind = '{Step.kind()}')
        );
END;

-- A dependency edge's source/sink kinds must be one of:
-- - file -> step
-- - step -> file
-- - static_tree -> file
-- This also rules out self-loops,
-- since source and sink always have different kinds under this rule.
-- Edges are only ever inserted or bulk-deleted, never updated in place,
-- and deletion cannot violate a kind-combination rule,
-- so an _ins trigger is the only one needed here.
CREATE TRIGGER IF NOT EXISTS dependency_check_kinds_ins AFTER INSERT ON dependency
BEGIN
    SELECT RAISE(ABORT, 'invalid dependency source/sink kind combination')
    FROM node AS s, node AS k
    WHERE s.i = NEW.source AND k.i = NEW.sink
        AND NOT (
            (s.kind = '{File.kind()}' AND k.kind = '{Step.kind()}')
            OR (s.kind = '{Step.kind()}' AND k.kind = '{File.kind()}')
            OR (s.kind = '{StaticTree.kind()}' AND k.kind = '{File.kind()}')
        );
END;

-- Reusable scratch tables for batch lookups keyed by a list of paths or node ids,
-- used instead of `json_each(...)`, which was found to be slow in performance tests.
-- Created once here and only ever cleared with `DELETE FROM` before reuse:
-- dropping and recreating a temp table on every call would invalidate SQLite's
-- prepared-statement cache (see `safe_update` in scheduler.py for the same convention).
CREATE TEMP TABLE IF NOT EXISTS path_list (path TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TEMP TABLE IF NOT EXISTS node_list (i INTEGER PRIMARY KEY) WITHOUT ROWID;
"""


# Find the UNCONFIRMED inputs of a step that were created by a static tree.
# No recursion through the dependency graph is needed:
# a dependency edge always connects a file and a step (dependency_check_kinds_ins enforces this),
# so a step's unconfirmed inputs are always among its direct inputs.
UNCONFIRMED_INPUTS = f"""
SELECT node.i, node.label FROM node
JOIN node AS cnode ON node.creator = cnode.i
JOIN file ON node.i = file.node
JOIN dependency ON node.i = source
WHERE sink = ? AND node.kind = '{File.kind()}'
    AND file.state = {FileState.UNCONFIRMED.value}
    AND cnode.kind = '{StaticTree.kind()}'
"""

# Flags the `_check_after` bit of every step with an in-scope output
# (declared DEFAULT or not; see below) under a directory target,
# for `Workflow.reconcile_targets()`.
# Newly-matching direction only:
# the stale direction (a step elevated by a directory target in a previous run)
# is already covered by that method's `_implied_need = TARGET` reset.
#
# Reaches the step through the same dependency-based join as UPDATE_CHECK_AFTER's
# directory arm (scheduler.py), `depo.source -> step`,
# not through the exact-target loop's file.creator() walk,
# so reconcile and recompute can never disagree about which step a file belongs to.
# What counts as an output there is the shared `REGULAR_OUTPUT_WHERE` predicate (file.py).
# No `need = DEFAULT` filter here:
# over-flagging is safe (recomputation is state-free)
# and UPDATE_CHECK_AFTER re-applies the restriction.
# No GraphError arms, per the best-effort decision for directory targets.
#
# The CROSS JOIN is load-bearing:
# a plain JOIN lets SQLite pick node-first join order
# and scan every file node via node_kind_label (kind=?) alone.
# CROSS JOIN forces SCAN target_dir
# -> SEARCH onode USING INDEX node_kind_label (kind=? AND label>? AND label<?)
# -> indexed probes of file and dependency_sink_source.
# (Claim validated using sqlite 3.51.2)
RECONCILE_TARGET_DIRS = f"""
UPDATE step SET _check_after = 1
WHERE node IN (
    SELECT depo.source
    FROM target_dir
    CROSS JOIN node AS onode ON (
        onode.kind = '{File.kind()}'
        AND onode.label >= target_dir.path
        AND onode.label < target_dir.upper
    )
    JOIN dependency AS depo ON depo.sink = onode.i
    JOIN file AS ofile ON ofile.node = onode.i
    WHERE {REGULAR_OUTPUT_WHERE}
)
"""

OBSERVABLE_STATE_VALUES = ", ".join(str(state.value) for state in sorted(OBSERVABLE_FILE_STATES))
"""The values of `OBSERVABLE_FILE_STATES`, formatted for an SQL `IN` clause.

Interpolated into a query instead of bound as parameters,
because the number of placeholders would otherwise follow the size of the set.
The values are integers from an `IntEnum`, so there is nothing to escape.
"""


class _HashTransitionError(Exception):
    """A hash update that `_reject_impossible` cannot make sense of.

    It carries only the reason why the combination is impossible.
    `Workflow.update_file_hash` turns it into a `ConsistencyError`
    that also names the file the update was about.
    """


@attrs.frozen
class _HashTransition:
    """What must happen to a file when a fresh hash arrives."""

    new_state: FileState = attrs.field()
    """The state to store for the file."""

    mark_creator: bool = attrs.field(default=False)
    """Whether the step that creates the file must be marked pending."""

    mark_consumers: bool = attrs.field(default=False)
    """Whether the steps that consume the file must be marked pending."""


_STATIC_TRANSITIONS = {
    True: _HashTransition(FileState.CONFIRMED, mark_consumers=True),
    False: _HashTransition(FileState.MISSING, mark_consumers=True),
}
"""How a static file changes when a fresh hash arrives, keyed by whether it is on disk.

A static file is an input only, so nobody has to rebuild it,
but every consumer has to reconsider the content it just changed to.
The key has no cause axis, because the cause does not enter into it.
"""


_OUTPUT_TRANSITIONS = {
    (HashUpdateCause.OBSERVED, True): _HashTransition(FileState.PLANNED, mark_creator=True),
    (HashUpdateCause.OBSERVED, False): _HashTransition(
        FileState.PLANNED, mark_creator=True, mark_consumers=True
    ),
    (HashUpdateCause.SUCCEEDED, True): _HashTransition(FileState.BUILT, mark_consumers=True),
    (HashUpdateCause.FAILED, True): _HashTransition(FileState.OUTDATED),
    (HashUpdateCause.FAILED, False): _HashTransition(FileState.PLANNED),
}
"""How an output changes when a fresh hash arrives, keyed by cause and by presence on disk.

An OBSERVED output becomes PLANNED rather than OUTDATED,
because what is on disk is not what its step wrote,
so there is no product hash left worth keeping.
Only a step's own run can leave a file OUTDATED,
which is what upholds the guarantee documented at `FileState.OUTDATED`:
a stored hash of an OUTDATED file still tells whether the file on disk is the one the step wrote.

`(SUCCEEDED, False)` has no row because `_reject_impossible` rejects it.
"""


def _reject_impossible(cause: HashUpdateCause, old_state: FileState, hash_known: bool) -> None:
    """Raise `_HashTransitionError` when this combination cannot arise.

    Parameters
    ----------
    cause
        Why the file was hashed.
    old_state
        The state of the file before the update.
    hash_known
        Whether the hash could be computed, i.e. whether the file is present on disk.

    Raises
    ------
    _HashTransitionError
        When the combination cannot arise in a consistent workflow.

    Notes
    -----
    The guards come in two kinds, which is why some rejections here look stricter than others.
    A role guard is unconditional:
    there is simply no role-appropriate state to move the file to,
    whatever the submitters happen to do.
    A cause guard only says what today's submitters can produce,
    so it is written down only where an actual submitter backs it up.
    OBSERVED therefore has no cause guard at all:
    any file in the STATIC or OUTPUT role can turn up on disk in any state,
    and reporting what is there is exactly what an observation does.
    """
    role = FILE_ROLE_BY_STATE.get(old_state)
    if role is None:
        # UNDECLARED is the one state without a role, so there is no role-appropriate
        # state to move the file to, whatever the new hash turns out to be.
        raise _HashTransitionError("an UNDECLARED file has no role in the workflow")
    if role == FileRole.VOLATILE:
        # Volatile outputs are exempt from hashing by definition, see `FileState.VOLATILE`.
        raise _HashTransitionError("volatile outputs are never hashed")

    if cause == HashUpdateCause.OBSERVED:
        # Nothing further to check: an observation reports what is on disk,
        # and every state left here is one a file can be in when that happens.
        return
    if cause not in (HashUpdateCause.SUCCEEDED, HashUpdateCause.FAILED):
        # Reaching this means a cause was added to `HashUpdateCause` without a guard here,
        # which no input can cause and no caller can fix, so it stops here.
        raise _HashTransitionError(f"no transition rule for cause {cause!r}")

    # What remains are the two causes that report on a step's own run,
    # which is only ever rehashed for that step's own outputs.
    if role != FileRole.OUTPUT:
        raise _HashTransitionError("only outputs are hashed after a step ran")
    if old_state == FileState.BUILT:
        # `Step.reset_for_rerun` demotes BUILT outputs before a step runs,
        # `Workflow.mark_step_pending` does the same for a step that is skipped,
        # and an output amended while the step runs starts out PLANNED.
        raise _HashTransitionError("an output of a step that ran or was skipped is not BUILT")
    if cause == HashUpdateCause.SUCCEEDED and not hash_known:
        # A step that did not write all of its declared outputs is not a success:
        # `Executor._compute_out_step_hash` clears `run.success`,
        # which turns the cause into FAILED.
        raise _HashTransitionError("a succeeded step leaves none of its outputs behind")


def _hash_transition(
    cause: HashUpdateCause, old_state: FileState, hash_known: bool
) -> _HashTransition:
    """Look up how a file state changes when a fresh hash arrives, and what must follow.

    Call `_reject_impossible` first:
    this function only looks up the row that the combination selects,
    and a combination that has no row raises `KeyError` instead of explaining itself.

    Parameters
    ----------
    cause
        Why the file was hashed.
    old_state
        The state of the file before the update.
    hash_known
        Whether the hash could be computed, i.e. whether the file is present on disk.

    Returns
    -------
    transition
        The new state and the follow-ups that `Workflow.update_file_hash` must take.

    Notes
    -----
    Three rules run through the tables, and are pinned by `test_hash_transitions_invariants`.
    Together they decide every field of every row,
    so a row cannot be edited without contradicting one of them.

    - A file that ends up available as an input (CONFIRMED or BUILT) marks its consumers,
      because the content they last ran with is not necessarily the content that is there now.
      An update that leaves the file unavailable has no such news:
      a consumer cannot run at all until the file becomes available again,
      and whatever makes it available marks the consumers itself.
    - A file that is not on disk marks its consumers under every cause but FAILED,
      because a consumer that ran with the file cannot have been right.
      Under FAILED the file was not usable as an input before the update and still is not,
      so no consumer has news.
    - An output marks its creator only under OBSERVED,
      the one cause that reports news from outside the creating step.
      SUCCEEDED and FAILED report on that step's own run,
      whose outcome already governs whether it runs again.
    """
    if FILE_ROLE_BY_STATE[old_state] == FileRole.STATIC:
        return _STATIC_TRANSITIONS[hash_known]
    return _OUTPUT_TRANSITIONS[cause, hash_known]


_DECLARABLE_STATES = (FileState.UNCONFIRMED, FileState.PLANNED, FileState.VOLATILE)
"""The file states a declaration may ask for, in `Workflow._declare_file`.

A file only ever enters the graph in one of these three,
and reaches the others by a later transition.
"""

_UNDECLARABLE_STATE_HINTS = {
    FileState.BUILT: "It must be PLANNED first.",
    FileState.CONFIRMED: "It must be UNCONFIRMED first.",
    FileState.MISSING: "It must be UNCONFIRMED first.",
}
"""The state a file must be declared in to reach an undeclarable one, keyed by that state.

Only covers the states that a caller could plausibly ask for by mistake.
A state without an entry still raises, just without the extra advice.
"""

_RELEVANT_STATES = OBSERVABLE_FILE_STATES | {FileState.UNDECLARED}
"""States in which a change to an attached file node can affect the workflow.

Wider than `OBSERVABLE_FILE_STATES` by exactly `UNDECLARED`,
which is the one state where a change is news without being worth hashing:
the file has no role, so no hash of it can be applied,
but the change may still affect the nglob patterns that match its path.
"""

_RELEVANT_STATES_DURING_BUILD = frozenset({FileState.CONFIRMED, FileState.MISSING})
"""States in which a change observed during a build phase can only be a user edit.

Stricter than `_RELEVANT_STATES`, because a build phase is writing outputs concurrently.
"""


def _relevant_states(during_build: bool) -> frozenset[FileState]:
    """Select the file states in which a change to an attached file node matters.

    Parameters
    ----------
    during_build
        Whether the change was observed while a build phase was running.
        The build is writing its own outputs then,
        so only a change to a static file can be news.

    Returns
    -------
    relevant_states
        `_RELEVANT_STATES_DURING_BUILD` or `_RELEVANT_STATES`.
    """
    return _RELEVANT_STATES_DURING_BUILD if during_build else _RELEVANT_STATES


#
# Error message formatting
#
# Everything from here to `_SupplyInfo` is pure text formatting:
# no database access and no `self`.
# Its job is to turn a rejected declaration into a message a plan author can act on,
# and to do so identically whichever of the two colliding declarations arrives second.
#

_FILE_ROLE_VERBS = {
    FileRole.STATIC: "declared static",
    FileRole.OUTPUT: "built",
    FileRole.VOLATILE: "declared volatile",
}
"""How each role is phrased, as a verb that takes `by <creator>`.

The order in which two colliding declarations are mentioned comes from `FileRole`'s values,
not from this table's insertion order: `_file_collision_message` sorts the two declarations,
and `FileRole` is an `IntEnum`, so that sort is by role value
(`STATIC` < `OUTPUT` < `VOLATILE`).
The entries here, and the keys of `_FILE_COLLISION_HINTS`, follow that same order,
which is what lets a hint be written for one direction of a pair only.
Reordering the `FileRole` values without reordering both tables would break the pairing.
"""

_FILE_COLLISION_HINTS = {
    (FileRole.STATIC, FileRole.STATIC): "Drop one of the two static() calls.",
    (
        FileRole.STATIC,
        FileRole.OUTPUT,
    ): "Drop the static() call, or write the step's output elsewhere.",
    (
        FileRole.STATIC,
        FileRole.VOLATILE,
    ): "Drop the static() call, or write the volatile output elsewhere.",
    (FileRole.OUTPUT, FileRole.OUTPUT): "Give each output a path of its own.",
    (FileRole.OUTPUT, FileRole.VOLATILE): "Pick one of the two.",
    (FileRole.VOLATILE, FileRole.VOLATILE): "Give each volatile output a path of its own.",
}
"""The way out of each collision, keyed by the two roles in `FileRole` value order.

Only the way out, never a restatement of the collision:
`_file_collision_message` already names both declarations in front of the hint.

Every hint here assumes both declarations are the plan's to change.
When one of them is not, `_STEPUP_COLLISION_HINTS` takes over.
"""

_STEPUP_COLLISION_HINTS = {
    FileRole.STATIC: "Drop the static() call.",
    FileRole.OUTPUT: "Write the step's output elsewhere.",
    FileRole.VOLATILE: "Write the volatile output elsewhere.",
}
"""The way out of a collision with a declaration StepUp made itself.

Keyed by the role of the other declaration, the one a plan author wrote:
that declaration is the only one that can be changed,
so a hint that offers a choice between the two would send the reader after a `static()`
call that does not exist in the plan.
"""


def _static_tree_file_message(tree_path: str, path: str) -> str:
    """Format the error for a static file declaration colliding with a static tree.

    Whichever of the two declarations comes second must produce this exact text:
    a byte-identical message is what makes the diagnostic independent of execution order.

    Parameters
    ----------
    tree_path
        The static tree's label, with a trailing slash.
    path
        The file inside the tree that a `static()` call tried to declare.

    Returns
    -------
    message
        The error message.
    """
    return (
        f"Static tree ({tree_path}) and static file ({path}) cannot both be declared: "
        "a static tree is the sole owner of the files under it, in either order. "
        "Drop the file declaration; use glob() to list files inside a tree."
    )


def _static_tree_product_message(tree_path: str, path: str) -> str:
    """Format the error for a build product colliding with a static tree.

    The mirror of `_static_tree_file_message` for the other collision: a step output
    or volatile output under a tree, in either declaration order.

    Parameters
    ----------
    tree_path
        The static tree's label, with a trailing slash.
    path
        The build product inside the tree.

    Returns
    -------
    message
        The error message.
    """
    return (
        f"Static tree ({tree_path}) cannot contain build product ({path}): "
        "a static tree is the sole owner of the files under it, in either order. "
        "Move the output outside the tree, or narrow the tree."
    )


def _glob_product_message(pattern: str, glob_step_label: str, path: str, step_label: str) -> str:
    """Format the error for a glob pattern that matches a path a step builds.

    The pattern and the output can be declared in either order,
    and whichever comes second is the one that raises.
    A byte-identical message is what makes the diagnostic independent of that order,
    so this is the single place the text is written.

    Parameters
    ----------
    pattern
        The glob pattern, as registered.
    glob_step_label
        The label of the step that registered the pattern.
    path
        The matching path that is a build product.
    step_label
        The label of the step that builds `path`.

    Returns
    -------
    message
        The error message.
    """
    return (
        f"Glob pattern ({pattern}) registered by step ({glob_step_label}) "
        f"matches ({path}), which step ({step_label}) builds. "
        "A glob pattern may only match static files: narrow the pattern, "
        "or declare the file with static() instead of building it."
    )


def _creator_phrase(kind: str, label: str) -> str:
    """Name the node that declared a file, in terms a plan author recognizes.

    Parameters
    ----------
    kind
        The creator's `Node.kind()`.
    label
        The creator's label.

    Returns
    -------
    phrase
        The creator, as a noun phrase that can follow `by`.

    Raises
    ------
    ConsistencyError
        When the creator is of any other kind, which is a bug in StepUp:
        only a step and the root node are things a plan author can be pointed at.
    """
    if kind == Step.kind():
        return f"step ({label})"
    if kind == Root.kind():
        return "StepUp itself"
    raise ConsistencyError(f"Cannot phrase a creator of kind {kind}: {label}")


@attrs.define(frozen=True, order=True)
class Decl:
    """A file declaration, with its creator named for the plan author."""

    role: FileRole = attrs.field()
    """The role in which the file is declared."""

    creator: str = attrs.field()
    """The declaring node, as returned by `_creator_phrase`."""

    authored: bool = attrs.field(default=True)
    """Whether a plan author wrote this declaration and can therefore drop it.

    It is `False` for the declarations StepUp makes itself while booting,
    which no plan can remove.
    """

    @classmethod
    def from_node(cls, role: FileRole, creator: Node) -> "Decl":
        """Describe a declaration made by a node that exists in the graph.

        Parameters
        ----------
        role
            The role in which the file is declared.
        creator
            The node that declared the file.

        Returns
        -------
        decl
            The declaration.
        """
        kind = creator.kind()
        return cls(role, _creator_phrase(kind, creator.label), authored=kind != Root.kind())


@attrs.define(frozen=True)
class Claim:
    """A file declaration that already exists in the graph."""

    role: FileRole = attrs.field()
    """The role of the attached file node."""

    creator: Node = attrs.field()
    """The node that declared the file."""


def _file_collision_message(path: str, decl_a: Decl, decl_b: Decl) -> str:
    """Format the error for two declarations of the same file that cannot coexist.

    The text is independent of the order in which the two declarations were made:
    the roles are mentioned in `FileRole` value order and, when both declarations
    have the same role, the two creators are sorted.
    This matches `_static_tree_file_message`, for the same reason:
    a plan that is wrong is wrong in either order, so it deserves the same diagnostic.

    Parameters
    ----------
    path
        The (normalized) path of the file that both declarations claim.
    decl_a, decl_b
        The two colliding declarations.
        Which one already exists and which one is new does not matter.

    Returns
    -------
    message
        The error message.

    Raises
    ------
    ConsistencyError
        When both declarations are identical, which is a bug in StepUp:
        redeclaring a file in the same role by the same creator is a no-op,
        not a collision, and must be skipped before reaching here.
        Also when neither declaration is a plan author's,
        since StepUp declares a single file while booting and cannot collide with itself.
    """
    decl1, decl2 = sorted([decl_a, decl_b])
    verb1 = _FILE_ROLE_VERBS[decl1.role]
    verb2 = _FILE_ROLE_VERBS[decl2.role]
    if decl1.role == decl2.role:
        if decl1.creator == decl2.creator:
            raise ConsistencyError(f"Identical declarations of file ({path}) are a no-op.")
        clash = f"cannot be {verb1} by both {decl1.creator} and {decl2.creator}"
    elif decl1.creator == decl2.creator:
        clash = f"cannot be both {verb1} and {verb2} by {decl1.creator}"
    else:
        clash = f"cannot be both {verb1} by {decl1.creator} and {verb2} by {decl2.creator}"
    if decl1.authored and decl2.authored:
        hint = _FILE_COLLISION_HINTS[decl1.role, decl2.role]
    elif decl1.authored:
        hint = _STEPUP_COLLISION_HINTS[decl1.role]
    elif decl2.authored:
        hint = _STEPUP_COLLISION_HINTS[decl2.role]
    else:
        raise ConsistencyError(f"StepUp's own declarations of file ({path}) collide.")
    return f"File ({path}) {clash}. {hint}"


def _duplicate_step_message(step_label: str, creator_a: str, creator_b: str) -> str:
    """Format the error for a step that is defined while an identical one exists.

    Like `_file_collision_message`, the text does not depend on which of the two
    definitions came first: the two creators are sorted.

    Parameters
    ----------
    step_label
        The label (command, with a workdir comment if any) both definitions share.
    creator_a, creator_b
        The creators of the two definitions, as returned by `_creator_phrase`.

    Returns
    -------
    message
        The error message.
    """
    creator1, creator2 = sorted([creator_a, creator_b])
    if creator1 == creator2:
        clash = f"is defined twice by {creator1}"
    else:
        clash = f"is defined by both {creator1} and {creator2}"
    return (
        f"Step ({step_label}) {clash}. "
        "A step is identified by its command and working directory: "
        "drop one of the two definitions."
    )


def _duplicate_static_tree_message(tree_path: str, creator_a: str, creator_b: str) -> str:
    """Format the error for a static tree that is registered while an identical one exists.

    Like `_duplicate_step_message`, the text does not depend on which of the two
    registrations came first: the two creators are sorted.

    Parameters
    ----------
    tree_path
        The static tree's label, with a trailing slash, that both registrations share.
    creator_a, creator_b
        The creators of the two registrations, as returned by `_creator_phrase`.

    Returns
    -------
    message
        The error message.
    """
    creator1, creator2 = sorted([creator_a, creator_b])
    return (
        f"Static tree ({tree_path}) cannot be declared by both {creator1} and {creator2}. "
        "A static tree has a single creator: drop one of the two declarations."
    )


def _claim_collision_message(path: str, claim: Claim, decl: Decl) -> str:
    """Format the error for a declaration of `path` that collides with an existing claim.

    Parameters
    ----------
    path
        The (normalized) path of the file that both declarations claim.
    claim
        The claim that already exists, as returned by `Workflow._existing_claim`.
    decl
        The declaration of `path` being made now.

    Returns
    -------
    message
        The error message.
    """
    if isinstance(claim.creator, StaticTree):
        # A static tree owns every path under it,
        # so what is violated here is the tree's ownership,
        # not the exclusivity of two ordinary declarations.
        # Saying so keeps the advice sensible: there is no static() call to drop.
        if decl.role == FileRole.STATIC:
            # `declare_static_files` hands a static declaration inside a tree over to that tree,
            # so only a build product can still collide with a tree here.
            raise ConsistencyError(f"Static declaration ({path}) not handed to its tree.")
        return _static_tree_product_message(claim.creator.label, path)
    # `_creator_phrase` has no phrase for a static tree,
    # so the claim may only be turned into a `Decl`
    # after the branch above has taken the tree creators out.
    claim_decl = Decl.from_node(claim.role, claim.creator)
    return _file_collision_message(path, claim_decl, decl)


def _raise_if_dir_inputs(inp_paths: Collection[str]) -> None:
    """Raise when an input path is a directory.

    The wording matches `api.py`'s client-side `PathError`,
    so a plan author sees the same sentence
    whether the mistake is caught before or after the RPC.

    Parameters
    ----------
    inp_paths
        The input paths declared in this call, sorted,
        which makes the reported path deterministic.

    Raises
    ------
    GraphError
        When a path ends in a separator.
    """
    for inp_path in inp_paths:
        if inp_path.endswith(os.sep):
            raise GraphError(f"Directory inputs are not supported: {inp_path}")


def _raise_if_out_and_vol_overlap(
    creator_phrase: str, out_paths: Collection[str], vol_paths: Collection[str]
) -> None:
    """Raise when the same path is declared as a regular and as a volatile output.

    Parameters
    ----------
    creator_phrase
        The node making both declarations, as returned by `_creator_phrase`.
    out_paths, vol_paths
        The regular and volatile output paths declared in this call.

    Raises
    ------
    GraphError
        When a path appears in both `out_paths` and `vol_paths`.
    """
    overlap = set(out_paths) & set(vol_paths)
    if len(overlap) > 0:
        first_collision = min(overlap)
        raise GraphError(
            _file_collision_message(
                first_collision,
                Decl(FileRole.OUTPUT, creator_phrase),
                Decl(FileRole.VOLATILE, creator_phrase),
            )
        )


@attrs.define
class _SupplyInfo:
    """Result of the `_supply_files` method.

    All fields are a snapshot of the database at the moment the file was supplied.
    They are not kept in sync with later changes to the graph,
    so a `_SupplyInfo` is meant to be consumed right away and not stored.
    """

    file: File = attrs.field()
    """A new or existing file."""

    state: FileState = attrs.field()
    """The state of the file when it was supplied."""

    detached: bool = attrs.field()
    """Whether the file was detached when it was supplied.

    A detached file describes a former life at best,
    so its state alone does not decide whether it can be used as an input.
    """

    new_idep: int | None = attrs.field()
    """Dependency identifier when the relation is new, None otherwise."""

    @property
    def availability(self) -> Availability:
        """Whether the file can serve as an input to the step; see `Availability`."""
        if self.detached:
            return Availability.UNAVAILABLE
        if self.state == FileState.UNCONFIRMED:
            return Availability.UNCONFIRMED
        if self.state in (FileState.BUILT, FileState.CONFIRMED):
            return Availability.AVAILABLE
        return Availability.UNAVAILABLE


def mark_dir_to_be_deleted(to_be_deleted: dict[str, FileHash | None], path: str):
    """Mark a directory as a candidate for removal in the next cleanup pass.

    The directory is only removed if it is empty by then,
    so marking one that other files still live in is harmless.
    The project root and its ancestors are never marked:
    StepUp may build outside the root, but it never owns the directories above it.

    Parameters
    ----------
    to_be_deleted
        The deletion queue to add the directory to.
    path
        The directory to mark.
    """
    path = Path(path).normpath()
    # `normpath` collapses everything it can, so any `..` left is a leading component.
    # A path made of nothing but `..` therefore points at an ancestor of the project root,
    # just like `.` points at the root itself.
    if path == "." or set(path.split(os.sep)) == {os.pardir}:
        return
    to_be_deleted[path + os.sep] = None


@attrs.define(frozen=True, order=True)
class GlobViolation:
    """A recorded glob match that no static declaration justifies."""

    step_label: str = attrs.field()
    """The label of the step that registered the pattern."""

    pattern: str = attrs.field()
    """The glob pattern, as registered."""

    path: str = attrs.field()
    """The offending match, relative to the root, directories with a trailing slash."""

    state: FileState | None = attrs.field(default=None, order=False)
    """The state of the attached file node, or `None` when the match has no node."""

    @property
    def is_error(self) -> bool:
        """Whether the match is a build product, i.e. an error rather than a warning.

        A violation with no node (`state` is `None`) is a warning:
        nothing in the graph contradicts the match, it is merely unjustified.
        A violation whose node is in the STATIC role is not a violation at all
        and is never constructed.
        """
        return self.state is not None and FILE_ROLE_BY_STATE[self.state] != FileRole.STATIC


@attrs.define(eq=False)
class Workflow(Trellis):
    """Represent StepUp's dual graph with the current state of the workflow."""

    dir_queue: asyncio.Queue | None = attrs.field(kw_only=True)
    """Directories to be watched can be added to this queue."""

    defer_cap: int = attrs.field(kw_only=True, default=100)
    """Maximum number of consecutive defers (since the last SUCCEEDED)
    before a step is failed instead of parked in PENDING again.
    A livelock guard against a step that defers forever,
    not expected to bind in normal use.
    """

    targets: frozenset[Path] = attrs.field(kw_only=True, factory=frozenset, converter=frozenset)
    """The paths `stepup build` was asked to produce.

    This is set once at construction and never mutated afterward.
    The only way to change the target set is to restart the director.
    An empty set (the default) means "build everything".
    """

    target_dirs: frozenset[Path] = attrs.field(kw_only=True, factory=frozenset, converter=frozenset)
    """The directories `stepup build` was asked to produce everything under.

    Entries always carry their trailing slash.
    This is set once at construction and never mutated afterward, mirroring `targets`.
    An empty set (the default) means no directory targets were given.
    """

    _to_be_deleted: dict[str, FileHash | None] | None = attrs.field(init=False, default=None)
    """The deletion queue being filled by an ongoing `delete_detached` call, if any.

    This is `None` outside `delete_detached`, so a `Node.before_delete` implementation
    that runs at any other moment fails loudly instead of queueing a path
    that nothing will ever act upon.
    See `to_be_deleted` for the layout of the queue.
    """

    @property
    def to_be_deleted(self) -> dict[str, FileHash | None]:
        """The deletion queue of the ongoing `delete_detached` call.

        Maps a path to its file hash.
        A key with a trailing separator is a directory,
        which is only removed when it turns out to be empty, and its hash is always `None`.
        Use `mark_dir_to_be_deleted` to add one.
        A key without a trailing separator is a file:
        BUILT/OUTDATED file nodes carry their file hash
        and VOLATILE file nodes carry `None`, meaning the file is removed whatever its content.

        Entries are keyed by path, not by node,
        so they are only meaningful for as long as the graph does not change underneath them.

        Raises
        ------
        AssertionError
            When there is no ongoing `delete_detached` call.
        """
        if self._to_be_deleted is None:
            raise AssertionError("No deletion queue outside Workflow.delete_detached().")
        return self._to_be_deleted

    #
    # Configuration and derived properties
    #

    @property
    def need_threshold(self) -> Need:
        """The need level above which a step's `_implied_need` makes it required."""
        return Need.DEFAULT if self.targets or self.target_dirs else Need.OPTIONAL

    #
    # Base class overrides and initialization
    #

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Trellis.default_node_classes(), File, Step, StaticTree]

    @classmethod
    def schema(cls) -> str:
        """Return the SQL schema for the database, including Workflow's own triggers."""
        return super().schema() + WORKFLOW_SCHEMA

    def _rebuild_temp_tables(self):
        """Seed `step_need_count` once per fresh connection, then chain to the base class."""
        super()._rebuild_temp_tables()

        # step_need_count (see STEP_SCHEMA / count_required_steps()) is a temp table,
        # empty on every fresh connection,
        # and only kept in sync with the step table going forward by triggers.
        # This can run more than once per connection (e.g. tests call initialize() more than once),
        # so it is unconditionally rebuilt from scratch here rather than assumed empty,
        # to stay correct (and idempotent) either way.
        self.db.execute("DELETE FROM step_need_count")
        self.db.execute(
            "INSERT INTO step_need_count (implied_need, succeeded, n) "
            "SELECT step._implied_need, step.state = ?, count(*) FROM node JOIN step "
            "ON node.i = step.node WHERE NOT node.detached GROUP BY 1, 2",
            (StepState.SUCCEEDED.value,),
        )

    def _check_consistency(self):
        """Check whether the initial graph satisfies all constraints."""
        strict = is_debug()
        super()._check_consistency()

        # Verify that the outputs of succeeded steps are all BUILT or VOLATILE.
        sql = (
            "SELECT file.state, fnode.label, snode.i, snode.label FROM node AS fnode "
            "JOIN file ON fnode.i = file.node JOIN dependency ON fnode.i = sink "
            "JOIN node AS snode ON snode.i = source JOIN step ON step.node = snode.i "
            "WHERE step.state = ? AND file.state NOT IN (?, ?) AND NOT fnode.detached"
        )
        data = (StepState.SUCCEEDED.value, FileState.BUILT.value, FileState.VOLATILE.value)
        to_mark_pending = set()
        for file_state_value, flabel, si, slabel in self.db.execute(sql, data):
            file_state = FileState(file_state_value)
            if strict:
                raise ConsistencyError(
                    f"{file_state.name} output of succeeded step: path_out={flabel} step={slabel}"
                )
            logger.error(
                "%s output of succeeded step: path_out=%s step=%s", file_state.name, flabel, slabel
            )
            to_mark_pending.add(Step(self, si, slabel))

        # Rerun the steps that seem to be out of date, despite being marked succeeded.
        for step in to_mark_pending:
            self.mark_step_pending(step)

    def delete_detached(self) -> dict[str, FileHash | None]:
        """Delete all detached nodes that can be removed safely.

        This includes a cleanup of static tree files that are no longer used,
        after which the regular `Trellis.delete_detached()` is called
        to remove any other detached nodes.

        Returns
        -------
        to_be_deleted
            The paths queued by the `Node.before_delete` implementations of the deleted nodes.
            See `to_be_deleted` for the layout of this queue.
        """
        if self._to_be_deleted is not None:
            raise AssertionError("Workflow.delete_detached() cannot be nested.")
        self._to_be_deleted = {}
        try:
            # Get rid of static tree files that are no longer used.
            for st in self.nodes(StaticTree):
                files = sorted(st.products(), reverse=True, key=(lambda node: node.path))
                for file in files:
                    if not any(file.sinks()):
                        file.detach()
            super().delete_detached()
            return self._to_be_deleted
        finally:
            self._to_be_deleted = None

    def initialize_boot(self) -> bool:
        """Initialize the (new) boot script.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        command = "." / PLAN_PY
        # Both nodes are looked up by label rather than among the root's products.
        # Nothing else can hold these two labels while attached:
        # a second declaration of plan.py collides in `_check_declaration`,
        # and a second definition of the boot step in `_raise_if_step_exists`.
        plan_file = self.find_attached(File, PLAN_PY)
        boot_step = self.find_attached(Step, Step.adjust_label(command))
        if (
            plan_file is not None
            and boot_step is not None
            and plan_file.get_state() == FileState.CONFIRMED
        ):
            # The boot steps are already present (from a previous invocation of stepup).
            return False

        # Need to (re)initialize the boot steps.
        for node in self.root.products():
            if node.i != self.root.i:
                node.detach()
        to_check = self.declare_static_files(self.root, [PLAN_PY])
        for path, file_hash in to_check.items():
            self.update_file_hash(path, file_hash.refreshed(path), cause=HashUpdateCause.OBSERVED)
        self.define_step(self.root, command, inp_paths=[PLAN_PY], need=Need.PLAN, _safe=True)
        return True

    #
    # Build targets
    #

    def _raise_if_forbidden_target(self, path: str, state: FileState):
        """Raise when `path` is a build target in a state a target may never have.

        Raises
        ------
        GraphError
            When `path` is in `self.targets` and `state` is in `TARGET_FORBIDDEN_STATES`.
        """
        if path in self.targets and state in TARGET_FORBIDDEN_STATES:
            if state == FileState.VOLATILE:
                raise GraphError(f"A build target cannot be a volatile output: {path}")
            raise GraphError(f"A build target cannot be a static file: {path}")

    def reconcile_targets(self):
        """Validate targets against the loaded graph and flag affected steps for recompute.

        Declaration-time validation (in `_declare_file` and `_resolve_supply_file`) only runs
        when `define_step`/`amend_step`/`declare_static_files` are actually called,
        which does not happen for a database-resumed run against an unchanged `plan.py`.
        Call this once at director startup:
        after the boot/resume step (`serve`'s `Workflow.initialize_boot`/`resume_from_db`),
        so that a changed `plan.py` has already been marked `PENDING`;
        after `Scheduler.initialize()` has created and populated the `target_dir` temp table;
        and before the first scheduler tick.
        It never computes elevation itself;
        elevation is derived, state-free recomputation (see `scheduler.UPDATE_CHECK_AFTER`)
        that runs on the next metadata pass for every step flagged here.

        Directory targets (`self.target_dirs`) are handled separately from `self.targets` below,
        by a single bulk range `UPDATE`.
        Unlike exact targets, directory-target elevation is best-effort and never raises.
        See `RECONCILE_TARGET_DIRS` comments for details.

        Raises
        ------
        GraphError
            When an exact target matches a `VOLATILE`, `CONFIRMED`, `MISSING` or `UNCONFIRMED`
            file whose creator chain has no `PENDING` step,
            i.e. the declaration producing that file state is not going to be re-evaluated.
            Never raised for directory targets.
        """
        # Stale TARGET values in _implied_need (from a previous run with different targets)
        # must be recomputed.
        # Over-flagging is always safe since recomputation is state-free.
        self.db.execute(
            f"UPDATE step SET _check_after = 1 WHERE _implied_need = {Need.TARGET.value}"
        )
        # One-time startup cost, several queries per target instead of one batched query.
        # Accepted for now given small typical target counts; revisit if this shows up in profiling.
        for path in sorted(self.targets):
            file = self.find_attached(File, path)
            if file is None:
                # Not (yet) in the graph, or detached.
                # Detached rows are deliberately skipped:
                # they may be garbage from an abandoned plan,
                # and raising on those would block legitimate builds.
                # Declaration-time checks and the not-produced warning cover these.
                continue
            state = file.get_state()
            if state in TARGET_FORBIDDEN_STATES:
                # Only raise when the declaration producing this row is still current:
                # a PENDING step in the creator chain may re-declare the file differently
                # when it reruns, in which case declaration-time checks take over.
                if not self._creator_chain_pending(file):
                    self._raise_if_forbidden_target(path, state)
                continue
            creator = file.creator()
            if isinstance(creator, Step):
                self.db.execute("UPDATE step SET _check_after = 1 WHERE node = ?", (creator.i,))

        # Directory targets: newly-matching outputs need a bulk range UPDATE.
        # See RECONCILE_TARGET_DIRS above for the query and its rationale.
        self.db.execute(RECONCILE_TARGET_DIRS)

    def _creator_chain_pending(self, node: Node) -> bool:
        """Return whether any step in the node's creator chain is PENDING."""
        while True:
            node = node.creator()
            if node is None or isinstance(node, Root):
                # Root.creator() returns Root itself, so this also terminates the walk.
                return False
            if isinstance(node, Step) and node.get_state() == StepState.PENDING:
                return True

    def is_regular_output(self, path: str) -> bool:
        """Return whether `path` is currently a regular (non-volatile) output of a step."""
        file = self.find_attached(File, path)
        return (
            file is not None
            and isinstance(file.creator(), Step)
            and file.get_state() in FILE_STATES_BY_ROLE[FileRole.OUTPUT]
        )

    def has_regular_output_under(self, dir_path: str) -> bool:
        """Return whether any active step's regular (non-volatile) output falls under `dir_path`.

        Parameters
        ----------
        dir_path
            A directory-target label (trailing slash).

        Returns
        -------
        has_output
            True if any active step produces a regular output under `dir_path`, False otherwise.
            False therefore means the directory target names a place nothing is built.

        Notes
        -----
        The query scans the label range `[dir_path, dir_range_upper(dir_path))`,
        i.e. exactly the file labels under the directory target,
        and applies the shared `REGULAR_OUTPUT_WHERE` predicate (`file.py`) within it.
        `is_regular_output()` answers the same question for one exact label,
        but reaches the step through the file's creator instead of a dependency edge.

        One filter of `scheduler.UPDATE_CHECK_AFTER`'s directory arm is deliberately
        not applied here: `step.need = DEFAULT`.
        Elevation leaves steps declared `OPTIONAL` alone,
        so a directory holding only `OPTIONAL` outputs elevates nothing,
        yet it is a correctly spelled path and must not be reported.
        The warning is meant to catch a mistyped or obsolete directory target
        ("nothing is ever produced here"),
        not a deliberate opt-out ("things are produced here, but you excluded them").
        This makes it weaker than the exact-target warning:
        it can stay silent for a directory target that ended up elevating no step at all.
        """
        row = self.db.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM node AS onode "
            "JOIN file AS ofile ON ofile.node = onode.i "
            "JOIN dependency AS depo ON depo.sink = onode.i "
            f"WHERE onode.kind = '{File.kind()}' "
            "AND onode.label >= ? AND onode.label < ? "
            f"AND {REGULAR_OUTPUT_WHERE})",
            (dir_path, dir_range_upper(dir_path)),
        ).fetchone()
        return bool(row[0])

    #
    # Workflow introspection
    #

    def format_dot_provenance(self) -> str:
        """Return the provenance graph (creator->product) in GraphViz DOT format."""
        node_sql = "SELECT i, kind, label FROM node"
        edge_sql = "SELECT creator, i FROM node"
        return self._format_dot_generic("empty", node_sql, edge_sql)

    def format_dot_dependency(self) -> str:
        """Return the dependency graph (source->sink) in GraphViz DOT format."""
        return self._format_dot_generic(
            "normal",
            "SELECT i, kind, label FROM node WHERE NOT (kind = 'root')",
            "SELECT source, sink FROM dependency "
            "JOIN node AS snode ON snode.i = source "
            "JOIN node AS cnode ON cnode.i = sink "
            "WHERE NOT ((snode.kind = 'file' AND snode.label LIKE '%/')"
            "OR (cnode.kind = 'file' AND cnode.label LIKE '%/'))",
        )

    def _format_dot_generic(self, arrowhead: str, node_sql: str, edge_sql: str) -> str:
        lines = [
            "strict digraph {",
            "  graph [rankdir=BT bgcolor=transparent]",
            "  node [penwidth=0 colorscheme=set39 style=filled fillcolor=5]",
            f"  edge [color=dimgray arrowhead={arrowhead}]",
        ]
        for i, kind, label in self.db.execute(node_sql):
            if label == "":
                label = kind
            label = json.dumps(textwrap.fill(label, 20))
            if kind == "step":
                props = ""
            elif kind == "file":
                props = " shape=rect fillcolor=9"
            elif kind == "st":
                props = " shape=octagon fillcolor=7"
            else:
                props = " shape=hexagon fillcolor=6"
            lines.append(f"  {i} [label={label}{props}]")
        for i, j in self.db.execute(edge_sql):
            lines.append(f"  {i} -> {j}")
        lines.append("}")
        return "\n".join(lines)

    def count_required_steps(self) -> tuple[int, int]:
        """Return completion counts of the required steps (succeeded and total).

        Only steps whose `_implied_need` exceeds `need_threshold` are counted,
        i.e. the steps this run is actually required to complete.

        Reads from `step_need_count`, a table of per-`(implied_need, succeeded)` bucket counts
        kept incrementally in sync with the `step` table by triggers (see `STEP_SCHEMA`),
        so this is a lookup over at most a handful of rows instead of a full step table scan.

        Returns
        -------
        nsucceeded
            The number of required steps that have succeeded.
        ntotal
            The total number of required steps.
        """
        sql = (
            "SELECT coalesce(sum(succeeded * n), 0), coalesce(sum(n), 0) "
            "FROM step_need_count WHERE implied_need > ?"
        )
        nsucceeded, ntotal = self.db.execute(sql, (self.need_threshold.value,)).fetchone()
        return nsucceeded, ntotal

    def steps(self, state: StepState) -> list[Step]:
        """Return all steps with the given state.

        The result is a list instead of a lazy cursor,
        so it is safe to iterate over it while mutating the graph (e.g. marking steps pending).
        """
        sql = (
            "SELECT i, label FROM node JOIN step ON node.i = step.node "
            "WHERE state = ? AND NOT detached"
        )
        return [Step(self, i, label) for i, label in self.db.execute(sql, (state.value,))]

    #
    # State propagation
    #

    def update_file_hash(self, path: str, new_fh: FileHash, *, cause: HashUpdateCause) -> bool:
        """Update the hash of one existing file and take the follow-up the change calls for.

        Parameters
        ----------
        path
            The path of the file whose hash must be updated.
        new_fh
            The new hash of the file.
        cause
            The reason for the hash update.

        Returns
        -------
        dropped
            Whether the update was dropped as an unchanged observation,
            which happens when `cause` is OBSERVED, the new hash equals the stored one
            and the file is no longer UNCONFIRMED.
            A dropped update leaves the file state and the rest of the graph untouched,
            though it may still refresh the cached `stat` properties of the stored hash.
        """
        # Detached rows are deliberately not filtered out.
        # A detached node keeps its state and hash and can be recycled back into the graph,
        # so its hash must stay current for the same reason mark_consuming_steps_pending
        # includes detached sinks.
        sql = (
            "SELECT node.i, file.state, file.hash FROM node JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND node.label = ?"
        )
        row = self.db.execute(sql, (path,)).fetchone()
        if row is None:
            raise ConsistencyError(f"Cannot update the hash of an unknown file: {path}")
        i, old_state, old_fh = row[0], FileState(row[1]), FileHash.from_json(row[2])

        # Reject a combination that cannot arise, before anything is written or skipped.
        hash_known = not new_fh.is_unknown
        try:
            _reject_impossible(cause, old_state, hash_known)
        except _HashTransitionError as exc:
            raise ConsistencyError(
                f"Unexpected file hash update: cause={cause.name} path={path} "
                f"state={old_state.name} "
                f"digest={fmt_short_digest(new_fh.digest)} "
                f"mode={stat.filemode(new_fh.mode)}: {exc}"
            ) from exc

        # An observation that changed nothing is not news, so it leaves the graph alone here.
        # Without this, every consuming step would be marked pending
        # on each startup scan, for files nobody touched.
        # UNCONFIRMED is the exception: only the update flips it to CONFIRMED or MISSING,
        # and its stored hash is deliberately kept from a previous CONFIRMED state
        # (see the `file_clear_hash` trigger),
        # so an equal hash is the normal case rather than a sign that nothing happened.
        # SUCCEEDED and FAILED are never dropped either:
        # an output whose content did not change still has to move to the state
        # that the outcome of its step calls for.
        if (
            cause == HashUpdateCause.OBSERVED
            and new_fh == old_fh
            and old_state != FileState.UNCONFIRMED
        ):
            # The content is unchanged, so the state stays put and no step has to reconsider
            # anything. Only the cached stat fields move forward, so that the next
            # `FileHash.refreshed` can short-circuit instead of digesting the file again.
            # The stat written back is the one taken before the file was read,
            # so a write that raced with the digest computation still forces a re-check.
            # An unknown hash never gets here, because `stat_differs` is false
            # between two unknown hashes,
            # which is what keeps `to_json`'s `None` out of a state whose CHECK forbids it.
            if new_fh.stat_differs(old_fh):
                self.db.execute("UPDATE file SET hash = ? WHERE node = ?", (new_fh.to_json(), i))
            return True

        # Actual update of the file state and hash.
        transition = _hash_transition(cause, old_state, hash_known)
        # `new_fh` is stored as-is for every transition:
        # the `file_clear_hash` trigger nulls the hash whenever the new state is
        # MISSING/PLANNED/VOLATILE,
        # so there is no need to special-case the stored hash for those target states here.
        logger.info(
            "Update file hash: cause=%s path=%s state=%s "
            "on_disk=%s mark_creator=%s mark_consumers=%s",
            cause.name,
            path,
            transition.new_state.name,
            hash_known,
            transition.mark_creator,
            transition.mark_consumers,
        )
        self.db.execute(
            "UPDATE file SET state = ?, hash = ? WHERE node = ?",
            (transition.new_state.value, new_fh.to_json(), i),
        )

        # Take the follow-ups the transition asks for.
        file = File(self, i, path)
        if transition.mark_creator:
            creator = file.creator()
            if isinstance(creator, Step):
                self.mark_step_pending(creator)
        if transition.mark_consumers:
            self.mark_consuming_steps_pending(file)
        return False

    def update_file_hashes(
        self, new_hashes: Mapping[str, FileHash], *, cause: HashUpdateCause
    ) -> set[str]:
        """Update the hashes of several existing files.

        Parameters
        ----------
        new_hashes
            The new hash of each file, keyed by path.
        cause
            The reason for the hash updates.

        Returns
        -------
        dropped
            The paths whose update was dropped as an unchanged observation,
            see `Workflow.update_file_hash`.
        """
        return {
            path
            for path, new_fh in new_hashes.items()
            if self.update_file_hash(path, new_fh, cause=cause)
        }

    def get_observable_file_hashes(self, paths: Collection[str]) -> dict[str, FileHash]:
        """Get the stored hashes of the files that can be observed on disk.

        Paths without a file node, and file nodes in a state outside `OBSERVABLE_FILE_STATES`,
        are left out of the result.

        Detachment is not part of the filter:
        a detached node can be recycled back into the graph,
        so its hash must stay current, see `Trellis.try_recycle`.

        Parameters
        ----------
        paths
            A list of paths.

        Returns
        -------
        file_hashes
            The current hashes of the observable files, keyed by path, ordered by path.
        """
        # The `label IN (SELECT path FROM path_list)` form makes the planner drive from
        # `node`'s `node_kind_label` index
        # (probed once per requested path via a Bloom-filtered membership test).
        # A plain JOIN against path_list lets the planner drive from a full scan of `node`
        # instead, an O(n_nodes) cost regardless of how few paths are requested.
        # As a bonus, results come out pre-sorted by the covering index,
        # so no separate ORDER BY sort is needed.
        # `path_list` is a real indexed scratch table (see `WORKFLOW_SCHEMA`),
        # populated here and cleared before reuse,
        # instead of `json_each(...)`, which was found to be slow in performance tests.
        # (Claim validated using sqlite 3.51.2)
        db = self.db
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
        sql = (
            "SELECT node.label, file.hash FROM node "
            "JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND node.label IN (SELECT path FROM path_list) "
            f"AND file.state IN ({OBSERVABLE_STATE_VALUES}) "
            "ORDER BY node.label"
        )
        return {path: FileHash.from_json(hash_value) for path, hash_value in db.execute(sql)}

    def get_all_observable_file_hashes(self) -> dict[str, FileHash]:
        """Get the stored hashes of every file in the workflow that can be observed on disk.

        The whole-workflow counterpart of `Workflow.get_observable_file_hashes`,
        with the same state filter and the same treatment of detached nodes.

        Returns
        -------
        file_hashes
            The current hashes of the observable files, keyed by path, ordered by path.
        """
        sql = (
            "SELECT node.label, file.hash FROM node "
            f"JOIN file ON file.node = node.i AND file.state IN ({OBSERVABLE_STATE_VALUES}) "
            "ORDER BY node.label"
        )
        return {path: FileHash.from_json(hash_value) for path, hash_value in self.db.execute(sql)}

    def mark_consuming_steps_pending(self, file: File):
        """Mark all steps that use this file as an input pending, detached ones included.

        Detached steps are included because detachment is not the end of a step:
        `Trellis.try_recycle` can reattach one when the plan re-declares it unchanged,
        and `Step.after_recycle` deliberately keeps its state and stored hash.
        Since only PENDING steps are ever hash-checked,
        a detached SUCCEEDED step whose input changed meanwhile
        would be recycled as up-to-date and never run again.
        Marking it pending now is what makes the recycled step reconsider itself.

        There is deliberately no variant of this method that skips the detached sinks,
        because every caller that marks consumers pending needs them for the reason above.
        """
        for step in file.sinks(Step, include_detached=True):
            self.mark_step_pending(step)

    def mark_step_pending(self, step: Step):
        """Make a SUCCEEDED or FAILED step PENDING (again).

        There can be many reasons for marking a step pending again, after having been completed:

        - inputs changed
        - outputs disappeared
        - environment variables changed

        Calls on RUNNING and CHECKING steps are ignored.

        This method also clears the deferred flag,
        which makes the step eligible for scheduling again.
        """
        # A RUNNING step reaches this point when it creates its own dynamic inputs.
        # CHECKING steps are mid hash-check and will settle naturally (SUCCEEDED or PENDING).
        state = step.get_state()
        if state in (StepState.RUNNING, StepState.CHECKING):
            return
        step.set_state(StepState.PENDING)
        if state in (StepState.SUCCEEDED, StepState.FAILED):
            logger.info("Mark %s step PENDING: %s", state.name, step.label)
            # Outdate the BUILT output files of the step.
            # Detached outputs are included for the same reason as in
            # mark_consuming_steps_pending: a detached node can be recycled back into the
            # graph with its state intact, so a BUILT output left behind here would be
            # recycled as up to date even though its producer has to run again.
            for file in step.sinks(File, include_detached=True):
                if file.get_state() == FileState.BUILT:
                    self.mark_file_outdated(file)

    def mark_file_outdated(self, file: File):
        """Make a BUILT file OUTDATED."""
        state = file.get_state()
        if state == FileState.BUILT:
            logger.info("Mark %s file OUTDATED: %s", state.name, file.path)
            file.set_state(FileState.OUTDATED)
            self.mark_consuming_steps_pending(file)
        elif state != FileState.OUTDATED:
            raise ConsistencyError(f"Cannot make file outdated when its state is {state.name}")

    #
    # Build phase (helper methods)
    #

    def _find_owning_static_tree(self, path: str) -> StaticTree | None:
        """Return the static tree that owns `path`, or None if none exists."""
        trees = []
        sql = (
            "SELECT i, label FROM node WHERE kind = 'st' AND NOT detached AND "
            "label = substr(?, 1, length(label))"
        )
        path = Path(path) / ""
        for i, label in self.db.execute(sql, (path,)):
            trees.append(StaticTree(self, i, label))
        if len(trees) > 1:
            raise GraphError(f"Multiple static trees match: {path}")
        if len(trees) == 1:
            return trees[0]
        return None

    def _existing_claim(self, path: str) -> Claim | None:
        """Look up the declaration that currently claims `path`.

        Every attached file node was declared by its creator in one of the three `FileRole` values,
        each of which claims the path exclusively.
        The roleless state `UNDECLARED` therefore cannot reach the lookup below:
        the `file_check_undeclared_detached_*` triggers keep every `UNDECLARED` file detached.

        Parameters
        ----------
        path
            The (normalized) path of the file.

        Returns
        -------
        claim
            The claim on `path`, or `None` when no attached file node claims `path`.
        """
        sql = (
            "SELECT file.state, cnode.i, cnode.kind, cnode.label "
            "FROM node JOIN file ON node.i = file.node "
            "JOIN node AS cnode ON cnode.i = node.creator "
            "WHERE node.kind = 'file' AND NOT node.detached AND node.label = ?"
        )
        row = self.db.execute(sql, (path,)).fetchone()
        if row is None:
            return None
        state, creator_i, creator_kind, creator_label = row
        role = FILE_ROLE_BY_STATE[FileState(state)]
        return Claim(role, self.node_from_row(creator_i, creator_kind, creator_label))

    def _check_declaration(self, creator: Node | str, path: str, role: FileRole) -> bool:
        """Check an intended declaration of `path` against the claim that already exists.

        This is the guard against two declarations claiming the same file.

        Parameters
        ----------
        creator
            The node declaring `path` now,
            or its phrase (as returned by `_creator_phrase`) when that node does not exist yet.
            A node may hold the existing claim itself; a phrase never does,
            because it names a creator that is not in the graph.
        path
            The (normalized) path of the file.
        role
            The role in which `creator` declares `path`.

        Returns
        -------
        is_new
            Whether `path` still needs to be declared.
            It is `False` when `creator` already claims `path` in `role`,
            in which case the caller must skip the declaration instead of repeating it.
            Roles never mix:
            re-declaring a static file as an output (or an output as volatile)
            remains a collision, even for the same creator.

        Raises
        ------
        GraphError
            When any other declaration claims `path`.
        """
        claim = self._existing_claim(path)
        if claim is None:
            return True
        if isinstance(creator, Node):
            if claim.role == role and claim.creator.i == creator.i:
                return False
            decl = Decl.from_node(role, creator)
        else:
            decl = Decl(role, creator)
        raise GraphError(_claim_collision_message(path, claim, decl))

    def _raise_if_step_exists(self, creator: Node, step_label: str) -> None:
        """Raise a friendly exception when an attached step node with the same label already exists.

        Parameters
        ----------
        creator
            The node defining the step now.
        step_label
            The label of the step, as returned by `Step.adjust_label`.

        Raises
        ------
        GraphError
            When an attached step node with this label already exists.
        """
        sql = (
            "SELECT cnode.kind, cnode.label FROM node JOIN node AS cnode ON cnode.i = node.creator "
            "WHERE node.kind = 'step' AND NOT node.detached AND node.label = ?"
        )
        row = self.db.execute(sql, (step_label,)).fetchone()
        if row is None:
            return
        creator_kind, creator_label = row
        raise GraphError(
            _duplicate_step_message(
                step_label,
                _creator_phrase(creator_kind, creator_label),
                _creator_phrase(creator.kind(), creator.label),
            )
        )

    def _resolve_supply_file(
        self,
        step: Step,
        path: str,
        require_new_edge: bool,
    ) -> tuple[File, FileState, bool, bool]:
        """Find or create the file for a path and resolve its relation to the step.

        The dependency edge is not inserted yet,
        so that the cyclic-dependency check can be batched over multiple paths.

        Parameters
        ----------
        step
            The step to supply to.
        path
            The path of the file that should supply to the step.
        require_new_edge
            When `True` the (file, step) dependency edge must not exist yet.
            If it does, a `GraphError` is raised.

        Returns
        -------
        file
            The existing or newly created file node.
        state
            The FileState of the file node.
        detached
            Whether the file node is detached.
        new_relation
            `True` when the (file, step) dependency edge does not exist yet
            and still needs to be inserted by the caller.

        Raises
        ------
        GraphError
            When the path is volatile.
            When the edge exists while it is required to be new.
        """
        file, detached = self.find_and_detached(File, path)
        st = self._find_owning_static_tree(path) if file is None or detached else None
        if st is not None:
            # An attached static tree owns the path, so the file is adopted by that tree.
            # A step can never declare an output inside a static tree (`_declare_file` refuses),
            # so this cannot take a product away from a creator that might still reclaim it.
            state = FileState.UNCONFIRMED
            self._raise_if_forbidden_target(path, state)
            file = self.create(File, st, path, state=state)
            detached = False
            self.watch_dir(Path(path).parent)
        elif file is None or file.creator() is None:
            # Nothing declares this path (anymore), so it has no role.
            # `creator=None` makes `Trellis.create` force `detached = True`.
            state = FileState.UNDECLARED
            file = self.create(File, None, path, state=state)
            detached = True
            self.watch_dir(Path(path).parent)
        else:
            # Either the file is attached, or it is detached but still has a creator,
            # which means it belongs to a subtree that may yet be recycled,
            # such as the output of a step whose plan has not been rerun yet.
            # Recreating it in that case would take the file away from its creator for good,
            # because `Trellis.create` clears the creator, cuts the sources
            # and invalidates the creating step's hash.
            # Supplying a file must never do that: only its creator decides what it owns.
            # A detached node is therefore reused as it is and stays detached,
            # hence unavailable, until its creator returns or it is deleted.
            state = file.get_state()
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            self._raise_if_forbidden_target(path, state)
        new_relation = (
            self.db.execute(
                "SELECT 1 FROM dependency WHERE source = ? AND sink = ?", (file.i, step.i)
            ).fetchone()
            is None
        )
        if not new_relation and require_new_edge:
            raise GraphError(f"Supplying file already exists: {path}")
        return file, state, detached, new_relation

    def _supply_files(
        self,
        step: Step,
        paths: Collection[str],
        require_new_edge: bool = True,
    ) -> list[_SupplyInfo]:
        """Find or create files for several paths and make them sources of the step.

        Parameters
        ----------
        step
            The step to supply to.
        paths
            The paths of the files that should supply to the step.
            Duplicates are not allowed.
        require_new_edge
            When `True` none of the (file, step) dependency edges is allowed to exist already.
            If one does, a `GraphError` is raised.

        Returns
        -------
        supply_infos
            Information about each supplied file, in the same order as `paths`.

        Raises
        ------
        GraphError
            When a path is volatile.
            When an edge exists while it is required to be new.
        CyclicError
            When adding the new relations would introduce a cyclic dependency.

        Notes
        -----
        Since `step` is the sink of every new edge in this batch,
        the cyclic-dependency check is performed once for the whole batch
        (via `Node.check_sources_acyclic`) instead of once per path.
        """
        resolved = [self._resolve_supply_file(step, path, require_new_edge) for path in paths]
        new_file_is = [file.i for file, _, _, new_relation in resolved if new_relation]
        if len(new_file_is) > 0:
            step.check_sources_acyclic(new_file_is)
        return [
            _SupplyInfo(
                file,
                state,
                detached,
                new_idep=(step.add_source(file, skip_cycle_check=True) if new_relation else None),
            )
            for file, state, detached, new_relation in resolved
        ]

    def _declare_file(self, creator: Node, path: str, file_state: FileState) -> File:
        """Create (or recycle) a file with an UNCONFIRMED, PLANNED or VOLATILE file state.

        Parameters
        ----------
        creator
            The creating step or static tree.
        path
            The (normalized) path. Directories must have trailing slashes.
        file_state
            The desired file state: `UNCONFIRMED`, `PLANNED` or `VOLATILE`.

        Returns
        -------
        file
            The created or recycled file node.

        Raises
        ------
        GraphError
            When `path` lies inside a static tree owned by another creator,
            when it is a build target that may not be in `file_state`,
            or when it lies under `.stepup`.

        Notes
        -----
        This does not check whether another declaration already claims `path`.
        That check requires the same claim lookup as deciding whether the declaration is new,
        so it is not repeated here.
        """
        # Consistency checks before creating the file.
        if file_state not in _DECLARABLE_STATES:
            hint = _UNDECLARABLE_STATE_HINTS.get(file_state, "")
            raise ConsistencyError(f"Cannot create a {file_state.name} file. {hint}".rstrip())
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if not isinstance(creator, StaticTree):
            static_tree = self._find_owning_static_tree(path)
            if static_tree is not None:
                if file_state == FileState.UNCONFIRMED:
                    raise GraphError(_static_tree_file_message(static_tree.label, path))
                raise GraphError(_static_tree_product_message(static_tree.label, path))
        self._raise_if_forbidden_target(path, file_state)
        if path.startswith(STEPUP_DIR + os.sep):
            raise GraphError(f"Cannot declare a file under {STEPUP_DIR}: {path}")

        file = self.create(File, creator, path, state=file_state)

        if file_state == FileState.VOLATILE:
            # Do not allow volatile files to have sinks.
            if any(file.sinks()):
                raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        else:
            # Watch parent directories of non-volatile files.
            self.watch_dir(Path(path).parent)
        return file

    def _hashes_to_check(self, unconfirmed: Collection[File]) -> dict[str, FileHash]:
        """Collect the currently known hashes of UNCONFIRMED file nodes, keyed by path.

        A known hash is what lets a check be skipped:
        `FileHash.refreshed` reuses it when size, inode and mtime are unchanged,
        so the file does not have to be read again to settle its state.

        Parameters
        ----------
        unconfirmed
            UNCONFIRMED file nodes that match a static tree.

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path, ordered by path.
        """
        db = self.db
        db.execute("DELETE FROM node_list")
        db.executemany("INSERT INTO node_list VALUES (?)", ((file.i,) for file in unconfirmed))
        sql = (
            "SELECT node.label, file.hash FROM node_list "
            "JOIN node ON node.i = node_list.i "
            "JOIN file ON file.node = node_list.i "
            "ORDER BY node.label"
        )
        return {path: FileHash.from_json(hash_value) for path, hash_value in db.execute(sql)}

    #
    # Build phase (low-level public API)
    #

    def declare_static_files(self, creator: Node, paths: Collection[str]) -> dict[str, FileHash]:
        """Declare files as unconfirmed static candidates, to be confirmed shortly after.

        A file declared here becomes CONFIRMED once confirmed present,
        or MISSING once confirmed absent,
        through a hash job submitted for its `to_check` entry (see `Workflow.update_file_hash`).

        Parameters
        ----------
        creator
            The node creating this file.
        paths
            The locations of the files or directories (ending with /).

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path.
            A path skipped because it is already declared static by the same creator
            contributes no entry here.

        Raises
        ------
        GraphError
            When a path lies inside a static tree owned by another creator,
            or when another declaration already claims it (`_check_declaration`).

        Notes
        -----
        Declaring a file that the same creator already declared static is a no-op.
        """
        # Sort paths to make the operation deterministic.
        paths = sorted(set(paths))
        # A path inside a static tree belongs to that tree, which is its sole owner,
        # so the declaration is handed over to the tree
        # and it does not matter whether the tree or the file was declared first.
        # Any other step declaring it static is an error,
        # again in either order (see `register_static_tree`).
        # The declarer is then the tree, which may already hold the claim itself:
        # `register_static_tree` takes over the paths under it as it is registered.
        to_declare = []
        for path in paths:
            declarer = creator
            if not isinstance(creator, StaticTree):
                static_tree = self._find_owning_static_tree(path)
                if static_tree is not None:
                    tree_creator = static_tree.creator()
                    if tree_creator is None or tree_creator.i != creator.i:
                        raise GraphError(_static_tree_file_message(static_tree.label, path))
                    declarer = static_tree
            # Repeating a static declaration is a no-op, so that overlapping declarations
            # (two patterns, or a pattern and its own literal match) compose instead of
            # colliding. The parent directory is already watched by the first declaration.
            if self._check_declaration(declarer, path, FileRole.STATIC):
                to_declare.append((declarer, path))
        # Define the files whose hashes must be checked.
        # `to_declare` follows the sorted `paths`, so the files are declared in path order.
        unconfirmed = [
            self._declare_file(declarer, path, FileState.UNCONFIRMED)
            for declarer, path in to_declare
        ]
        return self._hashes_to_check(unconfirmed)

    def register_static_tree(self, creator: Node, path: str) -> dict[str, FileHash]:
        """Install a static tree.

        Parameters
        ----------
        creator
            The step creating the static tree.
        path
            A path to a directory that will be treated as a static tree.

        Returns
        -------
        to_check
            The known hashes of the matching files whose existence and validity must be
            checked, keyed by path.

        Notes
        -----
        Registering a tree that the same creator's tree already covers is a no-op.
        """
        if has_any_wildcards(path):
            # `api.static()` expands patterns client-side,
            # so a wildcard can only get here through a bug or a hand-written RPC call.
            raise ConsistencyError(f"Static tree does not support wildcards: {path}")
        if path == STEPUP_DIR or path.startswith(STEPUP_DIR + os.sep):
            raise GraphError(f"Cannot declare a static tree under {STEPUP_DIR}: {path}")
        path = Path(path) / ""
        if path in ("./", ""):
            # A root tree would have to own plan.py and every step output,
            # which defeats the point of a static tree.
            # Reject it explicitly: without this check,
            # `_find_owning_static_tree`'s `substr(label, 1, length(label))` comparison
            # never matches an in-root label (which carries no `./` prefix),
            # so a root tree would otherwise silently own nothing and block nothing.
            raise GraphError(
                "A static tree cannot be the project root: it would have to own "
                "plan.py and every step output. Declare the subdirectories instead."
            )
        if path == "/":
            # The absolute counterpart of the case above.
            # An absolute static tree is supported, but a tree at the file system root
            # would own every absolute path in the graph, which is never what a plan means.
            raise GraphError(
                "A static tree cannot be the file system root: it would own every "
                "absolute path in the workflow. Declare a subdirectory instead."
            )
        static_tree = self._find_owning_static_tree(path)
        if static_tree is not None:
            own_creator = static_tree.creator()
            if own_creator is not None and own_creator.i == creator.i:
                # This creator already covers `path` with a static tree of its own,
                # so re-registering it adds nothing.
                return {}
            if static_tree.label == path:
                raise GraphError(
                    _duplicate_static_tree_message(
                        path,
                        _creator_phrase(own_creator.kind(), own_creator.label),
                        _creator_phrase(creator.kind(), creator.label),
                    )
                )
            raise GraphError(f"Static tree is a subdirectory of an existing static tree: {path}")
        clause, pattern = prefix_clause("node.label", path)
        sql = f"SELECT 1 FROM node WHERE kind = 'st' AND NOT detached AND {clause}"
        if self.db.execute(sql, (pattern,)).fetchone() is not None:
            raise GraphError(
                f"Static tree is a parent directory of an existing static tree: {path}"
            )
        # A static tree is the sole owner of the files under it.
        # Attached file nodes already present under this path are therefore
        # either this creator's own static declarations, which the tree takes over below,
        # or a violation.
        # Which of the two declarations came first only decides where the error is raised,
        # not what it says (see `_static_tree_file_message`).
        sql = (
            "SELECT node.i, node.label, node.creator, file.state "
            "FROM node JOIN file ON node.i = file.node "
            f"WHERE NOT node.detached AND {clause} "
            "ORDER BY node.label"
        )
        handover = []
        for node_i, existing_path, existing_creator, existing_state in self.db.execute(
            sql, (pattern,)
        ):
            if existing_state not in FILE_STATES_BY_ROLE[FileRole.STATIC]:
                raise GraphError(_static_tree_product_message(path, existing_path))
            if existing_creator != creator.i:
                raise GraphError(_static_tree_file_message(path, existing_path))
            handover.append(node_i)
        st = self.create(StaticTree, creator, path)
        # The creator declared these files itself, before declaring the tree that contains them.
        # The tree is their sole owner, so transfer them to it.
        # This is a deliberate bypass of Trellis.create():
        # going through it would treat the transfer as a recycle
        # and call Step.after_lost_product() on the old creator,
        # deleting the hash of the very step that is handing them over
        # and making it permanently unskippable.
        # Nothing is lost: the creator re-declares both the files and the tree on its next run,
        # so the creator column is simply reassigned.
        for node_i in handover:
            self.db.execute("UPDATE node SET creator = ? WHERE i = ?", (st.i, node_i))
        # Adopt matching detached file nodes, e.g. leftovers from a previous run.
        # Attached nodes owned by this creator were handed over just above;
        # any other attached node raised.
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE node.detached AND {clause}"
        )
        matching_paths = [path for (path,) in self.db.execute(sql, (pattern,))]
        return self.declare_static_files(st, matching_paths)

    def define_step(
        self,
        creator: Node,
        command: str,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        workdir: str = ".",
        need: Need = Need.DEFAULT,
        resources: dict[str, int] | None = None,
        shell: bool = False,
        env_overrides: dict[str, str] | None = None,
        duration: float | None = None,
        _safe: bool = False,
    ) -> dict[str, FileHash]:
        """Define a new step.

        Parameters
        ----------
        creator
            The step that generated this step.
            This is `self.root` for the boot script.
        command
            The command to execute.
        inp_paths
            Input paths.
        env_deps
            The environment variables used by the step.
        out_paths
            Output paths.
        vol_paths
            Volatile output paths: not reproducible, but cleaned up like output files.
        workdir
            The directory where the command must be executed,
            typically relative to the working directory of the director.
        need
            The need of the step; see `Need` in `enums.py` for details.
        resources
            The resources required by the step, e.g. {"cpu": 2, "gpu": 1}.
        shell
            Whether the command should be executed in a shell.
        env_overrides
            Step-specific environment variable overrides, e.g. {"OMP_NUM_THREADS": "4"}.
            These keys must not overlap with `env_deps`.
        duration
            An initial estimate of the step's wall time in seconds, used by the scheduler to
            prioritize execution order before any measurement is available.
            When `None`, a new step gets the column's default (1.0), while a recycled step
            keeps its previously measured (or given) duration.
        _safe
            The initial value for the `safe` field of the step.
            This is an internal field, not controlled by the end user.
            It is used to prevent steps from being queued if their creator is not
            RUNNING or SUCCEEDED.
            The only exception is the top-level `plan.py` step, which is always safe to queue.

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path.
        """
        # If it is a boot step, check that there was no boot step yet.
        if creator.i == self.root.i and any(self.root.products(Step)):
            raise GraphError("Boot step already defined.")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        env_deps = sorted(set(env_deps))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        # _declare_file applies the same check, but only on the path that reaches it.
        # This one is still needed because the `old_step is not None` branch below
        # returns early, and that path never calls _declare_file on vol_paths.
        # vol_paths is sorted, so the reported path does not depend on argument order.
        for vol_path in vol_paths:
            self._raise_if_forbidden_target(vol_path, FileState.VOLATILE)
        _raise_if_dir_inputs(inp_paths)
        if env_overrides is not None and not set(env_deps).isdisjoint(env_overrides):
            raise GraphError(
                "Variable(s) cannot be both an env dependency and a env_overrides override: "
                + ", ".join(sorted(set(env_deps) & set(env_overrides)))
            )
        if env_overrides is not None:
            reserved = set(env_overrides) & RESERVED_ENV_VARS
            if reserved:
                raise GraphError(
                    "Variable(s) set by StepUp cannot be overridden: " + ", ".join(sorted(reserved))
                )
        # Check overlap before the recycle short-circuit below,
        # so it applies uniformly to a fresh definition and a re-definition.
        step_label = Step.adjust_label(command, workdir)
        self._raise_if_glob_match(step_label, out_paths + vol_paths)

        # If a compatible detached step is found, fully recycle it, instead of creating a new one.
        # This restores the step and its products (recursively), preserving its edges,
        # state and stored hash.
        old_step = self.try_recycle(
            Step,
            creator,
            command,
            workdir=workdir,
            need=need,
            shell=shell,
            resources=resources,
            env_overrides=env_overrides,
            duration=duration,
            inp_paths=inp_paths,
            env_deps=env_deps,
            out_paths=out_paths,
            vol_paths=vol_paths,
        )
        if old_step is not None:
            # Look for UNCONFIRMED inputs that match a static tree.
            # Their existence still needs to be checked,
            # ideally confirmed by a hash job submitted for them.
            unconfirmed = {
                File(self, i, label)
                for i, label in self.db.execute(UNCONFIRMED_INPUTS, (old_step.i,))
            }
            return self._hashes_to_check(unconfirmed)

        # Validate the new step before creating it, so that every check that names the step
        # sees the graph without the step's own declarations in it.
        self._raise_if_step_exists(creator, step_label)
        step_phrase = _creator_phrase(Step.kind(), step_label)
        for out_path in out_paths:
            self._check_declaration(step_phrase, out_path, FileRole.OUTPUT)
        for vol_path in vol_paths:
            self._check_declaration(step_phrase, vol_path, FileRole.VOLATILE)
        _raise_if_out_and_vol_overlap(step_phrase, out_paths, vol_paths)

        # Create new step
        step = self.create(
            Step,
            creator,
            command,
            workdir=workdir,
            need=need,
            shell=shell,
            duration=duration,
            _safe=_safe,
        )
        step.set_resources(resources)
        step.set_env_overrides(env_overrides)

        # Keep track of all missing files that match a static tree and need to be confirmed.
        unconfirmed = set()

        # Supply inp_paths
        for info in self._supply_files(step, inp_paths):
            # We do not care about the unavailable files here,
            # because the step will only be executed when all inputs are available.
            if info.availability == Availability.UNCONFIRMED:
                unconfirmed.add(info.file)

        # Process vars
        step.add_env_deps(env_deps)

        # Create out_paths
        for out_path in out_paths:
            file = self._declare_file(step, out_path, FileState.PLANNED)
            file.add_source(step)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self._declare_file(step, vol_path, FileState.VOLATILE)
            file.add_source(step)

        logger.info("Define step: %s", step.label)
        return self._hashes_to_check(unconfirmed)

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        ran_concurrently: Callable[[int, int], bool],
    ) -> tuple[set[str], set[str], dict[str, FileHash]]:
        """Amend step information.

        Parameters
        ----------
        step
            The step specifying the additional info.
        inp_paths
            Additional input paths.
        env_deps
            Additional environment variables that the step is using.
        out_paths
            Additional output paths.
        vol_paths
            Volatile output paths: not reproducible, but cleaned up like output files.
        ran_concurrently
            Callable `(producer_node_i, consumer_node_i) -> bool`.
            Flags a `BUILT` input as unfresh if the producer step's execution window overlaps
            with the current step's, meaning the current step may have read stale content.

        Returns
        -------
        unavailable
            A set of input paths that are not available.
        unfresh
            A set of input paths that are available but fail the amend() freshness check.
        to_check
            The known hashes, keyed by path, of the files whose validity must still be checked,
            e.g. by submitting a hash job for each.
            A path that turns out to be MISSING belongs in `unavailable`:
            the caller must add it there after checking.

        Notes
        -----
        A pre-existing initial dependency stays an initial dependency and is ignored here,
        so it does not affect what `reset_for_rerun` drops.
        """
        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        _raise_if_dir_inputs(inp_paths)

        # Keep track of missing files, of which there are three different types:
        # - unavailable = certainly not available.
        # - unfresh = available, but fails the amend() freshness check.
        # - unconfirmed = possibly available but need to be checked.
        #   These are UNCONFIRMED files that need to be confirmed as CONFIRMED (or MISSING).
        unavailable = set()
        unfresh = set()
        unconfirmed = set()
        dynamic_ideps = []

        # Process inp_paths
        infos = self._supply_files(step, inp_paths, require_new_edge=False)
        for info in infos:
            availability = info.availability
            if availability == Availability.UNAVAILABLE:
                unavailable.add(info.file.path)
            elif availability == Availability.UNCONFIRMED:
                unconfirmed.add(info.file)
            elif info.state == FileState.BUILT:
                producer = info.file.creator()
                if isinstance(producer, Step) and ran_concurrently(producer.i, step.i):
                    unfresh.add(info.file.path)
            if info.new_idep is not None:
                dynamic_ideps.append((info.new_idep,))

        # Process vars
        step.amend_env_deps(env_deps)

        # Drop outputs the step already declares,
        # so that they are neither re-declared nor turned into dynamic dependencies.
        # This is done before the glob check because such an amendment adds nothing to the graph
        # and must therefore not be able to fail.
        out_paths = [
            path for path in out_paths if self._check_declaration(step, path, FileRole.OUTPUT)
        ]
        vol_paths = [
            path for path in vol_paths if self._check_declaration(step, path, FileRole.VOLATILE)
        ]
        _raise_if_out_and_vol_overlap(
            _creator_phrase(Step.kind(), step.label), out_paths, vol_paths
        )
        self._raise_if_glob_match(step.label, out_paths + vol_paths)

        # Create out_paths
        for out_path in out_paths:
            file = self._declare_file(step, out_path, FileState.PLANNED)
            new_idep = file.add_source(step)
            dynamic_ideps.append((new_idep,))

        # Create vol_paths
        for vol_path in vol_paths:
            file = self._declare_file(step, vol_path, FileState.VOLATILE)
            new_idep = file.add_source(step)
            dynamic_ideps.append((new_idep,))

        self.db.executemany("INSERT INTO dynamic_dep VALUES (?)", dynamic_ideps)
        return unavailable, unfresh, self._hashes_to_check(unconfirmed)

    #
    # Glob patterns
    #

    def nglob_registrations(self) -> Iterator[tuple[int, NamedGlob, Step]]:
        """Iterate over the patterns registered by all attached steps, with their context.

        Yields
        ------
        nglob_i
            The row identifier in the `nglob` table,
            needed to update the persisted matches of this registration.
        ng
            The pattern and its recorded matches.
        step
            The step that registered the pattern.
        """
        sql = (
            "SELECT node.i, label, nglob.i, data FROM node "
            "JOIN nglob ON node.i = nglob.node WHERE NOT node.detached"
        )
        for node_i, label, nglob_i, data in self.db.execute(sql):
            yield (
                nglob_i,
                json_converter.structure(json.loads(data), NamedGlob),
                Step(self, node_i, label),
            )

    def matches_any_glob(self, path: str) -> bool:
        """Test whether any registered glob pattern matches `path`.

        This is the relevance test for a path with no node of its own:
        a pattern declares none of its matches,
        so the pattern itself is the only record that the path is interesting.
        Only the stored regexes are consulted, so no match set has to be deserialized.
        """
        sql = (
            "SELECT nglob.regex FROM nglob JOIN node ON node.i = nglob.node WHERE NOT node.detached"
        )
        return any(re.compile(regex).fullmatch(path) for (regex,) in self.db.execute(sql))

    def register_nglob(self, step: Step, ng: NamedGlob) -> None:
        """Register a glob pattern used by a step and validate its matches.

        Parameters
        ----------
        step
            The step that called `glob()` (or `static()` with a pattern).
        ng
            The pattern and the matches found by the client's file system scan.

        Raises
        ------
        GraphError
            When a match is a known build product, or lies under the `.stepup` directory.

        Notes
        -----
        A pattern owns nothing: this only records the pattern, so the step becomes pending
        when the match set changes, and rejects matches that a glob may never see.
        Matches that are not (yet) justified are accepted here.
        `find_glob_violations` catches the ones that never become justified.
        """

        paths = ng.files()

        # A match cannot already be a known build product.
        # Detached nodes are excluded: their state is a memory of a former life, not a claim.
        if paths:
            db = self.db
            db.execute("DELETE FROM path_list")
            db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
            states = ", ".join(
                str(state.value)
                for state in FILE_STATES_BY_ROLE[FileRole.OUTPUT]
                | FILE_STATES_BY_ROLE[FileRole.VOLATILE]
            )
            # An attached node always has a creator, so the LEFT JOIN is only defensive.
            sql = (
                "SELECT node.label, creator.label FROM node "
                "JOIN file ON file.node = node.i "
                "LEFT JOIN node AS creator ON creator.i = node.creator "
                "WHERE node.kind = 'file' AND NOT node.detached "
                "AND node.label IN (SELECT path FROM path_list) "
                f"AND file.state IN ({states}) "
                "ORDER BY node.label LIMIT 1"
            )
            row = db.execute(sql).fetchone()
            if row is not None:
                path, creator_label = row
                raise GraphError(_glob_product_message(ng.pattern, step.label, path, creator_label))

        # Reject matches under .stepup/, mirroring the rule _declare_file applies.
        # This is load-bearing, not defensive:
        # NamedGlob does not skip dot entries the way the standard library's glob does.
        for path in paths:
            if path.startswith(STEPUP_DIR + os.sep):
                raise GraphError(
                    f"Glob pattern ({ng.pattern}) matches a path under {STEPUP_DIR}: {path}"
                )

        step.add_nglob(ng)

        self.watch_nglob_dirs(ng)

    def watch_nglob_dirs(self, ng: NamedGlob) -> set[str]:
        """Watch the directories in which `ng` could gain or lose a match.

        These are the parent of every current match,
        plus the pattern's base directory,
        so a zero-match pattern still notices its first match appearing.

        Returns
        -------
        dirs
            The directories handed to `watch_dir`.
        """
        # A directory match is watched one level up, unlike a declared directory node,
        # because the pattern selects the directory itself and not what it contains.
        dirs = {parent_dir(path.rstrip(os.sep)) for path in ng.files()}
        dirs.add(glob_base_dir(ng.pattern))
        for path in dirs:
            self.watch_dir(path)
        return dirs

    def persist_nglob_matches(self, nglob_i: int, step: Step, ng: NamedGlob):
        """Store the new matches of one registration and make its step run again.

        Parameters
        ----------
        nglob_i
            The row identifier in the `nglob` table, as yielded by `nglob_registrations`.
        step
            The step that registered the pattern.
        ng
            The pattern with its new matches.
        """
        step.delete_hash()
        data = (json.dumps(json_converter.unstructure(ng)), nglob_i)
        self.db.execute("UPDATE nglob SET data = ? WHERE i = ?", data)
        self.mark_step_pending(step)

    def _raise_if_glob_match(self, step_label: str, product_paths: Collection[str]) -> None:
        """Raise when a registered glob pattern matches a path a step is about to build.

        This is the late-arriving half of the rule that a glob pattern may only match static files.
        `register_nglob` catches the outputs that already exist.
        This method catches the ones declared afterwards.

        Whichever event happens second is the one that raises,
        which is what makes the rule independent of execution order.

        Parameters
        ----------
        step_label
            The label of the step declaring `product_paths`, or its command when the step node
            does not exist yet (`define_step`, before the recycle short-circuit).
        product_paths
            The output and volatile paths the step is about to declare.
        """
        if not product_paths:
            return
        sql = (
            "SELECT node.label, nglob.pattern, nglob.regex FROM nglob "
            "JOIN node ON node.i = nglob.node WHERE NOT node.detached"
        )
        for glob_step_label, pattern, regex in self.db.execute(sql):
            for path in sorted(product_paths):
                if re.compile(regex).fullmatch(path):
                    raise GraphError(
                        _glob_product_message(pattern, glob_step_label, path, step_label)
                    )

    def find_glob_violations(self) -> list[GlobViolation]:
        """Find recorded glob matches that no static declaration justifies.

        Runs once at the end of every build phase, over the persisted `nglob` table
        rather than over the patterns registered during this phase,
        which makes it order-independent and idempotent across restarts.
        See `finalize.report_unbuilt` for the reporting side.

        Returns
        -------
        violations
            The unjustified matches, sorted by (step label, pattern, path).
        """
        records = []
        paths = set()
        for _nglob_i, ng, step in self.nglob_registrations():
            for path in ng.files():
                records.append((step.label, ng.pattern, str(path)))
                paths.add(str(path))
        if len(records) == 0:
            return []

        # Resolve every match against the attached file nodes in one bulk query.
        # See get_observable_file_hashes for why this uses an IN subquery against path_list.
        db = self.db
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
        sql = (
            "SELECT node.label, file.state FROM node "
            "JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND NOT node.detached "
            "AND node.label IN (SELECT path FROM path_list)"
        )
        states = {path: FileState(value) for path, value in db.execute(sql)}

        # Static tree roots are few, so a prefix test in Python beats a join per match.
        tree_labels = [
            label
            for (label,) in db.execute("SELECT label FROM node WHERE kind = 'st' AND NOT detached")
        ]

        violations = []
        for step_label, pattern, path in records:
            state = states.get(path)
            if state is not None:
                if state in FILE_STATES_BY_ROLE[FileRole.STATIC]:
                    continue
            elif self._is_justified_without_node(path, tree_labels) or not Path(path).exists():
                # A match deleted during the build is not the user's fault to fix:
                # the watcher and the startup rescan already handle its disappearance.
                continue
            violations.append(GlobViolation(step_label, pattern, path, state))
        return sorted(violations)

    def _is_justified_without_node(self, path: str, tree_labels: list[str]) -> bool:
        """Test whether a match with no file node of its own is nevertheless justified.

        Parameters
        ----------
        path
            The match, root-relative, directories with a trailing separator.
        tree_labels
            The labels of all attached static trees, each with a trailing separator.

        Returns
        -------
        justified
            Whether `path` is (inside) a static tree, or a directory that contains a
            static tree or a static file.
        """
        # A) Inside a static tree, or a static tree root itself.
        # Appending a separator reproduces _find_owning_static_tree's `Path(path) / ""` exactly.
        probe = path if path.endswith(os.sep) else path + os.sep
        if any(probe.startswith(label) for label in tree_labels):
            return True
        if not path.endswith(os.sep):
            return False
        # B) Directory-only arms: the match contains a static tree, or a static file.
        # A root match ("./") contains every label, which the prefix range cannot express:
        # labels are root-relative and carry no "./" prefix,
        # so dir_range_upper("./") == ".0" would match nothing.
        # Drop the range instead of inventing a sentinel upper bound
        # (SQLite compares TEXT byte-wise by default, so no string is a reliable maximum).
        is_root = path in ("./", "/")
        if any(is_root or label.startswith(path) for label in tree_labels):
            return True
        states = ", ".join(str(state.value) for state in FILE_STATES_BY_ROLE[FileRole.STATIC])
        sql = (
            "SELECT 1 FROM node JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND NOT node.detached "
            f"AND file.state IN ({states}) "
        )
        args = ()
        if not is_root:
            sql += "AND node.label >= ? AND node.label < ? "
            args = (path, dir_range_upper(path))
        return self.db.execute(sql + "LIMIT 1", args).fetchone() is not None

    def process_nglob_changes(self, deleted: Collection[str], updated: Collection[str]):
        """Mark steps with nglob pending if they are affected by the deleted and updated paths.

        Parameters
        ----------
        deleted
            The deleted files.
        updated
            The created or modified files, i.e. the watcher's `updated` set.
            A path that did not exist before is a potential new match.
        """
        if deleted & updated:
            raise ConsistencyError("Deleted and updated paths cannot overlap.")
        for i, ng, step in self.nglob_registrations():
            # A step becomes pending when one of its patterns loses a deleted file as a match,
            # or could gain a new match among the updated files.
            evolved = ng.will_change(deleted, updated)
            if evolved is not None:
                self.persist_nglob_matches(i, step, evolved)

    #
    # Watch phase
    #

    def change_is_relevant(self, path: str, *, during_build: bool = False) -> bool:
        """Return whether a file system change to `path` can affect the workflow.

        An attached file node decides the answer through its state.
        A path with no attached node of its own is judged by the registered glob patterns,
        which is the only place such a path is recorded at all.

        Parameters
        ----------
        path
            The path that changed.
        during_build
            Whether the change was observed while a build phase was running,
            see `_relevant_states`.

        Returns
        -------
        is_relevant
            Whether the change can affect the workflow.
        """
        file = self.find_attached(File, path)
        if file is not None:
            return file.get_state() in _relevant_states(during_build)
        return self.matches_any_glob(path)

    def relevant_paths_under(self, directory: str, *, during_build: bool = False) -> Iterator[str]:
        """Iterate over all paths under `directory` whose disappearance is relevant.

        Both file nodes and the recorded matches of glob patterns are considered:
        a match has no node of its own, so the pattern is the only place it is recorded.
        A path that occurs in both is yielded only once.

        Parameters
        ----------
        directory
            The directory that was removed, with or without a trailing separator.
        during_build
            Whether the removal was observed while a build phase was running,
            see `_relevant_states`.
            The recorded glob matches are yielded in either case,
            because a pattern may not match a build product.
        """
        if not directory.endswith(os.sep):
            directory += os.sep
        states = ", ".join(str(state.value) for state in sorted(_relevant_states(during_build)))
        seen = set()
        clause, pattern = prefix_clause("node.label", directory)
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state IN ({states}) AND {clause} AND NOT detached"
        )
        for (path,) in self.db.execute(sql, (pattern,)):
            seen.add(path)
            yield path
        for _nglob_i, ng, _step in self.nglob_registrations():
            for path in ng.files():
                if path.startswith(directory) and path not in seen:
                    seen.add(path)
                    yield path

    #
    # Directory handling
    #

    def watch_dir(self, path: str):
        """Watch a directory, without creating it.

        The directory is handed to the watcher through `dir_queue`.
        It does not need to exist yet:
        the watcher remembers it and installs the watch as soon as it appears.
        """
        path = Path(path)
        if path == "":
            path = Path(".")
        if self.dir_queue is not None:
            self.dir_queue.put_nowait(path)

    def create_dirs(self, paths: Iterable[str]):
        """Create the directories at `paths` and watch them.

        Call this right before a step needs them,
        so a directory is only created when something is actually going to use it.
        The root directory is silently skipped: it always exists and is watched at startup.
        """
        dirs = {Path(path).normpath() for path in paths}
        dirs.discard(Path("."))
        for path in sorted(dirs):
            path.makedirs_p()
            self.watch_dir(path)

    def mark_dir_to_be_deleted(self, path: str):
        """Mark a directory as a candidate for removal in the ongoing `delete_detached` call."""
        mark_dir_to_be_deleted(self.to_be_deleted, path)
