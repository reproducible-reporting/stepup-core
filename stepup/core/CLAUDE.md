<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Architecture of StepUp Core

This file documents the design of `stepup/core/`:
how the modules are layered, how the processes fit together,
and the conventions that hold in every subpackage.
It complements the repo-wide conventions in the top-level `CLAUDE.md`.
Each subpackage has a `CLAUDE.md` of its own for what concerns only that subpackage.

## Layout and Layering

The modules of `stepup/core/` form three bands of top-level modules and seven subpackages:

```text
stepup/core/
  # band 1: shared vocabulary, imports no subpackage
  exceptions  enums  path  nglob  constants  cattrs  utils  stepinfo  tracebacks  hash
  # band 2: step-side API, imports band 1, rpc and platform only
  api  extapi  call  script  render_jinja
  # band 3: entry points
  __main__  pytest
```

A subpackage may import from band 1, from band 2 (except `platform` and `rpc`,
which band 2 depends on), and from the subpackages in this table:

| Subpackage | May import from |
| --- | --- |
| `platform` | no subpackage |
| `graph` | no subpackage |
| `rpc` | `platform` |
| `watch` | `platform` |
| `launch` | `graph`, `platform` |
| `cli` | `graph`, `platform`, `rpc`, `watch` |
| `director` | `graph`, `launch`, `platform`, `rpc`, `watch` |

`cli` and `director` never import each other:
they live in different processes and only talk over RPC.
`launch` imports `api.amend` and `extapi.get_local_import_paths` for the forkserver child,
which is why `api` and `extapi` may not import `graph`, `launch`, `director` or `cli`.
`test_layering` in `tests/test_conventions.py` enforces the bands and the table.

When a name is needed on the wrong side of this table, move it down instead of adding an edge:
a constant to `constants.py`, a small helper to `utils.py`,
and anything that depends on the platform to `platform/`.
The `__init__.py` of a subpackage holds a docstring and nothing else,
so a module is always imported by its full name, e.g. `from ..graph.step import Step`.

## `__all__`

Wildcard imports are banned (ruff `F403`), so `__all__` does not describe a star-import
surface here. It is the module's **import contract**: the names that code outside the module
is meant to import.

- Every module in `stepup/core/` declares `__all__`, placed directly after the imports and
  before `logger`. It is a tuple of string literals, sorted (enforced by ruff `RUF022`).
- List a name when it is imported by another `stepup` module, a downstream extension package,
  a user's `plan.py`, or a `pyproject.toml` entry point (e.g. `add_build_subcommand`).
- Do not list module-internal names, even when they lack a leading underscore:
  `logger`, SQL constants (`*_SCHEMA`, `SELECT_*`, ...), helpers used only within the module.
  A public-looking name is not a claim that the name is exported.
- Tests may import names that are not in `__all__`; white-box testing does not make a name
  part of the contract.
- Do not re-export: a name in `__all__` must be defined in that same module.
  Import a name from the module that defines it, not from a module that happens to import it.
- `__all__ = ()` is a real claim that nothing outside the module may import from it,
  and is correct only for leaf modules.

Consequence, enforced by `ConventionTests` in `stepup/core/pytest.py`
(subclassed in `tests/test_conventions.py`):
any `from .mod import X` or `from ..pkg.mod import X` inside `stepup/`
requires `X` to be in `mod.__all__`, with no exemption for underscore-prefixed names.
The same holds for a name imported from another package in the `stepup` namespace,
which is how an extension package inherits this convention.
When a module needs something private from another one,
move that name to a module both may depend on (`utils.py` is often the right home),
or promote it to part of the defining module's contract.

## Process Model

StepUp runs as two process types:

- **Director** (`director/`):
  An asyncio process that owns the workflow graph and SQLite database.
  It exposes an RPC server over a socket, whose path is handed to it by the TUI
  (a per-run temp directory, e.g. `tempfile.TemporaryDirectory(prefix="stepup-")`,
  not under `.stepup/`).
  Manages `Builder`, `Watcher`, and `Scheduler`.
  Steps run *inside* the director's event loop as asyncio tasks.
  The `Executor` that runs them and the hashing of their files
  are described in `stepup/core/director/CLAUDE.md`.
- **TUI** (`cli/build.py`, `cli/terminal.py`):
  Spawns the director as a subprocess and connects to its RPC socket as a client
  (e.g. to forward keyboard commands). It also serves the reporter RPC socket itself,
  to which the director connects as a client to report progress.
  Renders progress to the terminal.

The entry point `stepup build` (in `cli/build.py`) is what users run.
It spawns the director and connects to it.
(`stepup boot` still exists as a deprecated alias of `stepup build`.)

## Features That Depend on the Platform (`platform/oscompat.py`)

StepUp runs on Linux, macOS and Windows,
and the user-facing statement of what differs is `docs/platforms.md`.
Keep that page in step with any change to these features.

`platform/oscompat.py` holds one predicate per optional feature
(`af_unix_available`, `forkserver_available`, `file_modes_available`, `perf_available`,
`raw_terminal_available`, `suspend_available`, `windows_console_available`, ...),
which tests for the feature itself instead of for `sys.platform` where Python allows it.
Code that depends on such a feature calls the predicate,
and a feature that the user asks for without having it fails with a `UsageError`.
The tests use the same predicates for the capability markers of `tests/conftest.py`,
see `tests/CLAUDE.md`.

Do not write `sys.platform == "win32"` for a feature that has a predicate,
and add a predicate when a new feature has none.
A plain platform test is fine for what is a property of the platform as such,
e.g. the spelling of the paths in `path.py` or the choice of a Windows API in `launch/`.

## Naming Gotcha: `keep_going` and `carry_on`

Two similar flags have different meanings:

- `keep_going` (CLI `-k` / `--keep-going`, env `STEPUP_BUILD_KEEP_GOING`):
  keep building unrelated steps after a step has failed.
- `carry_on` (internal, part of the director's `amend_step()` RPC result):
  whether a running step may continue after amending its inputs,
  or must abort because some dynamic inputs are not yet available.

## Unreachable Branches

A branch that no caller can currently reach is dead code, and what to do with it depends on
what reaching it would mean:

- Reaching it would imply **a bug in StepUp**: keep the branch,
  but let it `raise ConsistencyError` instead of returning a plausible result.
  A fallback that quietly does something sensible lets the bug travel;
  a `ConsistencyError` stops it where it starts and keeps its traceback.
  Use `ConsistencyError`, not a bare `RuntimeError`:
  it is what marks an error as a bug in StepUp rather than in the user's plan.
  Examples in `graph/workflow.py`: `_creator_phrase` on a creator kind it has no phrase for,
  and `_file_collision_message` on two identical declarations,
  which its callers must already have skipped as a no-op.
- Reaching it would be **a case the callers happen not to produce**,
  handled correctly if they ever did: delete it.
  Whoever needs it later adds it back with the call site and the test that reaches it.

Do not keep a branch alive with a comment that it may become reachable again:
such a comment records a past state of the code, which the code no longer supports
and nothing keeps true.

## Directory Creation and Removal

StepUp has no node type for directories, yet it creates and removes the ones its steps need.
The two halves are deliberately symmetric:
**a directory is created when a step is about to use it,
and marked for removal when that step or its files leave the workflow.**

How both halves are implemented is in `stepup/core/director/CLAUDE.md`.

## File Path Considerations

### One Spelling on Every Platform

**Inside StepUp, a path is a string whose only separator is `/`, on every platform.**
On Windows, an absolute path looks like `C:/Users/me/project`, with an upper-case drive letter.
The workflow database, `nglob`, the affixes below, `dir_range_upper()`, `shq()`
and the expected outputs of the examples all rely on this.
Paths are compared as strings and are graph nodes,
so a path that differs from another one only by its separators would be a different node.

`Path` in `stepup/core/path.py` implements this.
It is a subclass of `Path` from the `path` library,
which StepUp uses instead of the built-in `pathlib`,
because `pathlib` normalizes away the affixes described below.
On POSIX, the subclass adds nothing.
On Windows, its constructor converts backslashes
and its methods use a variant of `ntpath` that returns `/`.

A string from the operating system is converted where it enters StepUp,
by the constructor of `Path`, by the `coerce_*()` functions or, for a plain string, by `to_slash()`.
Such strings come from `os.getcwd()`, globbing and directory listings, `sys` and module attributes,
environment variables, command-line arguments, `tempfile` and file watchers.
Windows accepts `/` in its file system functions, so nothing is converted back.

Consequences for code in `stepup/`, enforced by `ConventionTests.test_slash_paths`:

- Import `Path` from `stepup.core.path`, never from `path`.
- Write `"/"`, never `os.sep`.
- Use the methods of `Path` instead of `os.path`,
  whose functions return backslashes on Windows.
- Use `is_path_type()` to recognize a path object created by a user,
  who may have imported `Path` from the `path` library.
- Split a list of paths on `os.pathsep`, because `:` is part of `C:/` on Windows.

The Windows flavor is the class `_NtPath`, which is defined on every platform,
so that most of its behavior can be tested on Linux.
Such tests are necessary but not sufficient:
Windows runs C implementations of several `ntpath` functions,
for which other platforms use pure-Python fallbacks,
and the pure-Python functions differ between Python versions.
Run `tests/test_paths.py` in the Windows VM with the oldest and newest supported Python
(`WINVM_PYTHON`) after changing `path.py`.

Two properties of `_NtPath` are easy to break, which a comment in the class explains.

### Path Affixes

In some cases, path affixes must be preserved (leading `./` or trailing `/`).
The affixes are currently used in the places in StepUp:

- The `dst` argument of the `copy()` function in `stepup.core.api`, with
  a reusable mechanism for output path construction in `make_path_out()` in `stepup.core.path`.
- A local executable must contain at least one slash, e.g., `./script.sh` or `bin/script.sh`.
- The `getenv()` function in `stepup.core.api` preserves path affixes
  when reading environment variables must be treated as paths.
- A static tree path in the database is always stored with a trailing slash.

The `get_affixes()` and `apply_affixes()` functions in `stepup.core.path` are used to
extract and re-apply the affixes when needed.

## User-Facing API (`api.py`)

`plan.py` scripts call functions in `api.py` (e.g., `static()`, `step()`, `glob()`)
which send RPC calls to the director.

**Importing `api.py` must never connect to the director.**
The connection is opened by the first call to `get_rpc_client()`, which caches its result.

- The director imports `api.py` indirectly, through `launch/forkserver.py`,
  while `STEPUP_DIRECTOR_SOCKET` holds `DIRECTOR_SOCKET_SENTINEL`.
  Creating a client there raises `RuntimeError`.
- A forkserver child must open its own connection.
  Nothing may call `get_rpc_client()` before the fork,
  or every child would share the parent's socket.
- Console scripts such as `sc-render-jinja` may be invoked outside a build.

`install_excepthook()` is the one thing `api.py` still does at import time.
It is guarded by `_is_step_under_director()`, which only reads the environment,
because a step that fails before its first RPC call must still get a shortened traceback.

## Extension Developer API (`extapi.py`)

`extapi.py` collects utilities for authors of StepUp extension packages.
It is a curated surface, not a layer:
`subs_env_vars` (and the `EnvSubstitutor` it yields) is equally meant for extension authors,
yet lives in `api.py` because `extapi.py` imports from it.
`docs/reference/stepup.core.api.md` documents it under a separate heading.

`get_rpc_client` and `get_job_i` are **not** part of that surface.
They are internal to StepUp Core, and only appear in `api.__all__`
because other core modules (`extapi.py`, `cli/interact.py`) import them.
An extension talks to the director through the API functions, not through the RPC client.
