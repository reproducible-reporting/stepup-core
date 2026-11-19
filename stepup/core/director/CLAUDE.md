<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# The Director Process

This file documents `stepup/core/director/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Executor and Hashing

The director runs the steps and hashes their files inside its own process:

- **Executor** (`director/executor.py`):
  Runs each step as an asyncio task, tying the step lifecycle (skip/run/defer decisions,
  hash bookkeeping, reporting) together.
  A single `Executor` instance serves all concurrent steps; `--jobs` is the
  concurrency limit. Step child processes call back into the director over its RPC socket
  (e.g. `amend_step()`, `define_step()`).
  Launching the step's command and hashing its files are delegated to `launch/` and
  `hash.py` respectively.
- **Hashing** (`hash.py`):
  File/step hashing, which is blocking work, runs in a dedicated `ThreadWorker`,
  one thread per hash computation inside the director process, see `launch/worker.py`.
  The chunked digest loop releases the GIL and checks a cancel event between 256 KiB chunks,
  so hashes are concurrent and promptly interruptible.
  Only regular files are hashed, and a FIFO or a device is refused without waiting for it
  (`utils.open_regular_file`).
  The pure functions `compute_inp_hashes` / `compute_out_hashes` / `compute_both_hashes` are
  what actually runs inside a `ThreadWorker`; `Executor` wraps them via `_run_work_thread`.

## Directory Creation and Removal

The rule that both halves implement is stated in `stepup/core/CLAUDE.md`.

### Creation

`Executor._run_command` creates the working directory of a step
and the parent directories of its regular and volatile outputs,
right before launching the command.
The `amend_step` RPC does the same for outputs declared while the step is already running.
`Workflow.create_dirs` is the only place that creates a directory for a step.

Declaring a file (`_declare_file`, `_resolve_supply_file`), registering a glob pattern
and repopulating the directory queue at startup all merely *watch* a directory
(`Workflow.watch_dir`), which never creates anything.
Creating a directory earlier than the step that fills it means creating directories
for steps that never run.

The watcher takes the missing directories from there:
`WatcherBackend.dir_loop` records a directory that does not exist yet as an entry
without a watch, exactly like a directory whose watch has been dropped,
and watches the nearest existing ancestor instead.
`WatcherBackend.change_loop` installs the pending watch when the directory appears
and rescans its contents, so nothing created in the gap is missed.

### Removal

The deletion queue holds files and directories alike,
distinguished by a trailing separator on the key.
`Workflow.delete_detached` opens one for the duration of its own call
(reachable as `Workflow.to_be_deleted`, which raises at any other moment)
and returns it, so no queue outlives the cleanup pass that fills it.
`File.before_delete` marks the parent directory of a regular or volatile output.
`revert_optional_steps` builds a queue of its own and returns it,
because the nodes of the outputs it flags stay in the graph
and therefore never reach `before_delete`.
`Builder.finalize` merges the two queues and hands them to `remove_deletable_files`.

Whether a directory is marked depends on the role of the file, not on its state.
An output that the user removed by hand is `PLANNED`:
the file itself is then no longer in `to_be_deleted`, but its directory still is.
`File.initialize_row` keeps the state of a former output
when a node without a creator is recreated as `UNDECLARED`,
so the role survives until the node is deleted.
A former volatile output becomes `UNDECLARED` instead,
so that file and its directory are left alone.

`remove_deletable_files` deletes the files first and hands the directories to
`_prune_empty_dirs`, which removes each one only if it is empty,
and then walks up to the root doing the same,
because `Workflow.create_dirs` may have created several levels for one output.
The walk may also remove an empty ancestor that the user created,
since StepUp does not record which directories it created.
It never reaches a static tree, because no output can be removed from inside one:
`_declare_file` refuses an output inside an attached static tree,
and `register_static_tree` adopts the detached files under a new tree as static files.
The example `clean_dir_keep_static_tree` covers the latter,
and `stepup clean` relies on the same invariant for its own upward walk.
The project root and its ancestors are never removed either:
`mark_dir_to_be_deleted` refuses to queue them and the upward walk stops at them,
because a step may build outside the root, but the directories above it are not StepUp's to remove.
