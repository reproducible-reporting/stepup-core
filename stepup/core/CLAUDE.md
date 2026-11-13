<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Architecture of StepUp Core

This file documents the design of `stepup/core/`:
how the processes fit together, which invariants the workflow graph must satisfy,
and the conventions that govern the SQLite schema.
It complements the repo-wide conventions in the top-level `CLAUDE.md`.

## Process Model

StepUp runs as two process types:

- **Director** (`director.py`):
  An asyncio process that owns the workflow graph and SQLite database.
  It exposes an RPC server over a socket, whose path is handed to it by the TUI
  (a per-run temp directory, e.g. `tempfile.TemporaryDirectory(prefix="stepup-")`,
  not under `.stepup/`).
  Manages `Builder`, `Watcher`, and `Scheduler`.
  Steps run *inside* the director's event loop as asyncio tasks.
- **Executor** (`executor.py`):
  Runs each step as an asyncio task, tying the step lifecycle (skip/run/defer decisions,
  hash bookkeeping, reporting) together.
  A single `Executor` instance serves all concurrent steps; `--jobs` is the
  concurrency limit. Step child processes call back into the director over its RPC socket
  (e.g. `amend_step()`, `define_step()`).
  Launching the step's command and hashing its files are delegated to `run.py` and
  `hash.py` respectively (see below).
- **Hashing** (`hash.py`):
  File/step hashing, which is blocking work, runs in a dedicated `ThreadWorker`,
  one thread per hash computation inside the director process, see `run.py`.
  The chunked digest loop releases the GIL and checks a cancel event between 256 KiB chunks,
  so hashes are concurrent and promptly interruptible.
  The pure functions `compute_inp_hashes` / `compute_out_hashes` / `compute_both_hashes` are
  what actually runs inside a `ThreadWorker`; `Executor` wraps them via `_run_work_thread`.
- **TUI** (`tui.py`):
  Spawns the director as a subprocess and connects to its RPC socket as a client
  (e.g. to forward keyboard commands). It also serves the reporter RPC socket itself,
  to which the director connects as a client to report progress.
  Renders progress to the terminal.

The entry point `stepup build` (in `tui.py`) is what users run.
It spawns the director and connects to it.
(`stepup boot` still exists as a deprecated alias of `stepup build`.)

### RPC Transport (`rpc.py`)

The RPC servers listen on one of two transports,
which the TUI selects once with `select_rpc_transport()` (`STEPUP_RPC_TRANSPORT`)
and passes to the director with `--rpc-transport`:
a Unix domain socket where `socket.AF_UNIX` exists (the default),
or a TCP socket on `127.0.0.1` (the default and only choice on Windows).
Either way, a server is known by a *socket path*:
the Unix domain socket itself, or an endpoint file holding the port and a random token.
Either one appears atomically once the server listens,
so "the path exists" means "the server accepts connections" for both transports,
which is what the readiness check of the TUI and `query_director_log` rely on.
A Unix domain socket needs a rename for this (`SocketRPCServer._listen_unix`),
because `bind()` creates the socket file before `listen()`,
and a client that connects in between is refused.
Clients (steps, `stepup` subcommands, the director's reporter client)
tell the transports apart by the file type at the socket path,
so they need no setting, and `STEPUP_DIRECTOR_SOCKET` and the `SOCKET` line of the
director log have the same shape for both.

The request bodies are unpickled, so whoever can connect can run code in the server.
A TCP client therefore sends the token before anything else,
and the server reads nothing more from a connection with a wrong token.
The private temporary directory is the trust boundary for both transports.
Never put the token in an environment variable or on a command line.

Naming gotcha, two similar flags with different meanings:

- `keep_going` (CLI `-k` / `--keep-going`, env `STEPUP_BUILD_KEEP_GOING`):
  keep building unrelated steps after a step has failed.
- `carry_on` (internal, part of the director's `amend_step()` RPC result):
  whether a running step may continue after amending its inputs,
  or must abort because some dynamic inputs are not yet available.

## Command Line Interface (`__main__.py`, `tool.py`)

Each `stepup` subcommand is a **tool**, registered through a `stepup.tools` entry point
so that extension packages can add their own.
`tool.py` holds what the tools have in common (the `ToolFunc` signature,
`print_error`, and the read-only access to `GRAPH_DB`),
`__main__.py` parses the command line and dispatches to the tool.

**The exit status is decided by the exception that reaches `main()`,
and `__main__.py` is the only module that prints an error and picks an exit code.**

- A `ToolFunc` returns `None`. A return code is not a way to signal an error.
- A mistake the user can fix is raised as a `UsageError`, in a tool usually a `ToolError`.
  `main()` prints it as a short `ERROR:` message and exits with `ReturnCode.INTERNAL`.
- Any other exception keeps its traceback, because it is a bug in StepUp.
  `STEPUP_DEBUG` turns the previous case into this one.
- A tool that needs a return code of its own calls `sys.exit`,
  which `main()` lets through untouched.
  Only `build` does so, to report the `ReturnCode` bit flags of a build.
  `config` is the one tool that also renders its own errors,
  since showing a problem on the line of the setting it concerns is what the tool is for.

The point of concentrating this in `main()` is that a tool needs no error handling of its own.
A helper such as `interact.py`'s `_connect_director` restates one exception as
another, but never prints, exits, or decides whether a traceback is warranted.

## Step Launching and Interruption (`run.py`, `popen.py`)

`run.py` owns "run a step's command as a child process and return a `ChildOutcome`,"
independent of the step lifecycle in `executor.py`:
command classification (built-in copy vs. shell vs. `*.py` script vs. console-script entry point
vs. plain exec), spawning the subprocess or forkserver child, and capturing its output/return code/
resource usage. `launch_command()` is its single dispatch entry point, called from
`Executor._run_command()`.

`run.py` also defines `Worker`, the base class for anything that is the in-flight work of a
`Run` and can be interrupted by `Executor.interrupt()`: `SubprocessWorker` and
`ForkserverWorker` stop the underlying OS process; `ThreadWorker`
cancels a computation instead, so it overrides `interrupt()` directly
rather than using `Worker`'s delivery template.

### Stop Levels

The director does not pass signal numbers around, because Windows has no signals to speak of.
`DirectorHandler`, `Builder.stop` and `Executor.interrupt` use a `StopLevel` (`enums.py`),
which `Worker._stop` turns into what the platform offers:

| Level | POSIX | Windows |
| --- | --- | --- |
| `INTERRUPT` | `SIGINT` to the process group | `CTRL_BREAK_EVENT` to the process group |
| `TERMINATE` | `SIGTERM` to the process group | `CTRL_BREAK_EVENT` to the process group |
| `KILL` | `SIGKILL` to the process group | `TerminateJobObject` |

`describe_stop_level()` names the mechanism in the messages for the user.
A `ThreadWorker` cancels its computation at every level.

### POSIX: Sessions and Process Groups

Every step runs in a **session of its own**: `start_new_session=True` for subprocesses and
`os.setsid()` at the top of `_forkserver_entry` for forkserver children.
Both worker classes therefore signal a *process group* (`_signal_process_group`), not a single
pid, which is what reaches the actual work when a shell step is a pipeline or `&&`-chain that
keeps `sh` around as a wrapper.
The flip side is that the terminal no longer signals steps directly:
a Ctrl-C reaches only the TUI and the director, and the director is the only thing that stops
running steps (`DirectorHandler.interrupt`).

The same holds for a Ctrl-Z, with one extra twist.
`DirectorHandler.suspend` stops the steps before stopping itself
(`Worker.suspend` / `Executor.suspend`), and continues them after being continued.
It must use **`SIGSTOP`, not `SIGTSTP`**: a step's process group is orphaned by construction,
since its leader's parent (the director) lives in another session,
and the kernel discards `SIGTSTP` for an orphaned process group.
The director and the TUI stop *themselves* by re-raising `SIGTSTP` with the default
disposition, which keeps them a well-behaved job of the shell.
Anything that measures wall time across a suspension must discount it,
see `Executor.suspended_total`.

### Windows: Process Groups and Job Objects

Windows has no sessions and no forkserver.
Every step is a subprocess that is started with `CREATE_NEW_PROCESS_GROUP`
and put in a Job Object of its own (`winapi.JobObject`) right after its creation.
The two do different work:

- The **process group** is what `CTRL_BREAK_EVENT` is sent to.
  It also keeps the Ctrl-C of the console away from the step, as a session does on POSIX.
  A process that the step starts in another group is out of reach of the event.
- The **Job Object** holds everything the step starts, whatever its group.
  `KILL` terminates it, and the CPU time of the step is read from it,
  because Windows has no `os.wait4`.
  That CPU time counts in clock ticks of 15.6 ms and can exceed the wall time of a short step.
  Its limit `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` ends the steps when the director dies,
  even when the director itself is killed.

The rules below are easy to break, and `_stop_windows_step` explains them:

- `CTRL_BREAK_EVENT` travels through the console that the director shares with its steps.
  Without a console, sending it fails, and the step is killed instead.
  The director must therefore stay attached to the console of the TUI.
- Send the event only to a process that was started with `CREATE_NEW_PROCESS_GROUP`.
  Sent to another process, it went to every process of the console, the director included,
  when this was tested on Windows 11 24H2.
- A process that does not handle the event ends at once,
  with the status `STATUS_CONTROL_C_EXIT` and without cleaning up.
  A Python step gets no `KeyboardInterrupt`,
  and the `trap` of a shell step did not run with Git for Windows 2.56.
- A step that has finished may leave processes behind.
  Their job is kept open (`_retire_job`), so that they outlive their step, as on POSIX,
  and end with the director, unlike on POSIX.
- Ctrl-Z does not exist.
  `Worker.suspend` and `resume` do nothing where `oscompat.suspend_available()` is false.

### Windows: Console Control Events and Keys (`tui.py`, `director.py`)

The TUI and the director handle the signals of `oscompat.abort_signals()`
with `signal_handlers()` (`asyncio.py`),
which also serves the Proactor event loop, where `loop.add_signal_handler` does not exist.
On Windows, these signals are `SIGINT` and `SIGBREAK`,
which is how Python reports the console control events of Ctrl-C and Ctrl-Break.
`SIGTERM` is defined but never delivered, because a Windows process is terminated without notice.

- A console control event cannot be sent to one process.
  The console delivers it to all its processes, or to those of one process group.
  The TUI therefore starts the director without creation flags,
  in the same console and the same process group,
  and never forwards an event (`_terminal_broadcasts`).
  A director in a console or group of its own would not notice a Ctrl-C.
- Of the two, only a Ctrl-C is kept away from the steps by their process groups.
  A Ctrl-Break reaches them directly, at the same time as the director.
- A process that was started in a new process group ignores Ctrl-C,
  and so does everything it starts, because this setting is inherited.
  StepUp leaves the setting alone, so `stepup build` then only reacts to Ctrl-Break,
  much like a background job on POSIX.
  The tests undo it with `SetConsoleCtrlHandler` (`tests/console_host.py`),
  because a test runner may be in that state.
- Keys are read with `winapi.read_console_keys`, in a thread, and never with `msvcrt.getwch`.
  The latter switches off the input processing of the console for as long as it waits.
  With a thread waiting in `getwch`, a Ctrl-C arrived as the character `\x03`
  and no longer as an event, when this was tested on Windows 11 24H2.
- A console is recognized with `utils.is_terminal`, not with `isatty()`,
  which is also true for `NUL` on Windows.
- A console that goes away does not hang up its processes as a POSIX terminal does.
  When the SSH session of a build was closed, on Windows 11 24H2 with OpenSSH,
  the TUI ended at once, without a signal, and the director carried on in its watch phase.
  The TUI therefore puts the director in a Job Object of its own (`_confine_director`),
  as the director does with its steps, so that nothing of a build outlives `stepup build`.
- A killed process has no signal to show for it.
  `Process.kill()` gives the director exit code 1, a valid `ReturnCode`,
  so `TerminalSignalHandler` remembers that it killed the director.
  Any exit code that is not a `ReturnCode` combination, e.g. `0xC000013A`, becomes `INTERNAL`.

`tests/test_console.py` covers this end to end, as `tests/test_interrupt.py` does for POSIX.
It runs `stepup build` in a new console and generates the events and keys in it,
so it needs no interactive session.

### Encoding of the Standard Streams

`write_utf8_unless_terminal()` (`utils.py`) is called first thing by `stepup` and by the director.
A standard output or error that is not a terminal is then written in UTF-8 with `\n` line endings,
instead of in the locale encoding, which is the ANSI code page on Windows
with Python 3.14 and older and cannot represent what the reporter prints.
The environment is not used for this (`PYTHONIOENCODING`, `PYTHONUTF8`),
because the steps inherit it from the director.

### Commands on Every Platform (`popen.py`)

`popen_args()` turns a command into the `args` of `subprocess.Popen`.
`run.py` and `extapi.run_subprocess()` both use it, and never the `shell=True` of `subprocess`,
which is `cmd.exe` on Windows.

- A shell command runs as `[shell, "-c", command]` with the POSIX shell of `find_shell()`:
  the one that `STEPUP_SHELL` names, `/bin/sh`, or on Windows the shell that comes with Git.
  The search skips a `sh` in the Windows directory, because that one starts WSL.
  The search is lazy, so that a build without shell steps needs no shell,
  and a failed search fails the step that needed it, not the director.
- On POSIX, nothing else is translated,
  because the kernel finds programs and reads shebang lines.
- On Windows, `popen.py` does that work.
  A program without a directory is looked up on the `PATH` and `PATHEXT` of the step,
  not on those of the director, and not in the current directory,
  which `CreateProcess` and `shutil.which` both try first.
  A program with a directory is resolved in the working directory of the step.
  A script runs with the interpreter of its shebang line:
  `sh` and `bash` are those of `find_shell()`, `python` and `python3` are `sys.executable`,
  and anything else is looked up on `PATH`.

Windows hands a child one command line, which every program splits by rules of its own.
`subprocess` quotes a list of arguments for programs built with the C runtime of Microsoft.
For two other families, `popen.py` builds the command line itself:
programs on the Cygwin or MSYS2 runtime, such as the shell and the tools of Git for Windows,
and batch files, which `cmd.exe` runs.
Passing a list of arguments to `subprocess` for such a program changes the arguments,
e.g. a `*` is expanded and a `&` starts another command.

### Built-in Copy

The command `cp -p SRC DST` without a shell, which is what `copy()` creates, starts no process:
`launch_command()` copies the file in a `ThreadWorker`.
Starting a process for every copy is slow without a forkserver, and Windows has no `cp`.
The command stays what it is on every platform,
and with it the step label, the step hash and the expected outputs of the examples.
The copy follows `cp -p`, not `shutil.copystat` (see `_copy_file`),
and checks its cancel event between chunks, like the hashing in `hash.py`.

### Captured Output

`_decode` turns the bytes that a step wrote into text: it reads UTF-8 and drops what is invalid.
All captured output, also what a forkserver child collects as text,
passes through `_normalize_newlines`, which turns `\r\n` into `\n` on every platform,
so that the recorded output of a step does not depend on where or how it ran.

## Workflow Graph (`trellis.py`, `workflow.py`)

The core data structure is a combined **provenance** and **dependency** graph stored in SQLite.
`Trellis` (in `trellis.py`) is the abstract base implementing the graph, leveraging recursive SQL.
`Workflow` (in `workflow.py`) extends it with concrete node types:
`File` (`file.py`), `Step` (`step.py`),
and `StaticTree` (`static_tree.py`, used for inputs that are automatically declared as static).
Their states are defined in `enums.py`.

All graph mutations happen inside SQLite transactions.
The `DBSession` in `sqlite3.py` serializes writes.

A file node also has a **role** (`FileRole` in `enums.py`): who declares the file,
as opposed to where it currently sits in its lifecycle.
`FILE_STATES_BY_ROLE` and its inverse `FILE_ROLE_BY_STATE` are the only place where the
partitioning of `FileState` into roles is written down;
derive any "all states of this role" test from them instead of spelling out a state tuple.
Not every state set is a role, though:
"available as an input" (`BUILT`, `CONFIRMED`) cuts across two roles and includes neither fully,
and several other partial sets are genuinely about a lifecycle phase.
The rules for when a role determines a state, and why `UNDECLARED` is not simply
"the roleless state", are in the `FileRole` and `FileState` docstrings.

### Graph Determinism

**The graph must be a deterministic function of the source files and the plan/step code,
never of scheduling details: dispatch order, job count, resource limits or step durations.**
Running the same project with `-j1` and `-j16` must produce the same graph,
and so must two runs that change nothing,
whether the second starts from scratch or resumes a valid workflow database.
This constrains the *provenance* half of the graph as much as the dependency half:
which node is recorded as the creator of a file may not depend on
which of two concurrent steps happened to declare it first.

Note the requirement is deliberately stated relative to the source files and the code,
not in absolute terms:
`amend()` lets a step extend the graph based on what it discovers at run time,
so a step whose behaviour genuinely varies (on wall-clock time, on undeclared
environment state) will legitimately produce a different graph.
What must never vary is the graph produced by the *same* inputs and code.

The determinism goal applies to the graph of a **successful** build.
It does not extend to *how* an invalid workflow is reported:
when two steps' declarations conflict, whichever runs first may be the one that raises,
so which step fails, and where its traceback points, can depend on execution order.
What may not vary is whether the build succeeds or fails,
and, on success, the graph that results.
The *text* of such an error is nevertheless made order-independent whenever the raise site
knows both parties, since the plan is equally wrong in either order.

Why this matters:

- **Testability.**
  A deterministic graph is one that `expected_graph*.txt` files can pin down.
  A graph that depends on dispatch order can only be asserted loosely, or not at all,
  and it makes timing-sensitive tests flaky for reasons unrelated to what they test.
- **Debugging.**
  The graph is what a user inspects (`stepup graph`, `stepup browse`) when a build
  misbehaves. If it varies between runs, a reported graph no longer describes the run
  that produced it, and bug reports stop being reproducible.
- **Reproducibility.**
  The graph survives restarts and drives watch mode, so it is a durable artifact,
  not an internal detail. For a tool whose value proposition is persistent provenance,
  determinism of the graph *is* determinism of the outcome.
- **Avoiding spurious re-execution.**
  When a node changes creator, `Trellis.create` calls `after_lost_product()` on the old creator,
  which for a `Step` deletes its stored hash (`Step.after_lost_product`), so that step can no
  longer be skipped. A creator assignment that varies between runs therefore causes steps
  to re-run even though their inputs and outputs are unchanged.

The practical consequence for API design:
an operation that merely *observes* files (such as pattern matching) must not
acquire ownership of graph nodes, since ownership is what makes call order observable.
`StaticTree` is the model to follow: it owns the files under it regardless of
which step first referenced them, so its identity is order-independent by construction.

### Database Schema Versioning

The schema version is `Trellis.schema_version` (in `trellis.py`), written to the database via
`PRAGMA user_version`. On a version mismatch, the database is **wiped and recreated** from
scratch (`DBSession._wipe_database`), because there is no `ALTER TABLE` migration path.

Note that `DBSession.apply_schema()` re-executes the full schema (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, ...) via `executescript` on **every** database open, regardless of
whether `user_version` matched. A purely additive change (e.g. a new index) is therefore applied
lazily even to a database whose `schema_version` didn't change.
Bumping the version is a documentation/consistency convention for this project,
not strictly required for such a change to take effect.

**Policy: bump `schema_version` at most once per release.**
During a pre-release refactor, many commits may change the schema,
but they all share the single bumped version for the upcoming release;
do not bump the version again within the same release cycle.

**Claude Code must never bump `schema_version`.**
Deciding when a release's schema changes are complete is a human judgment call,
so only a human coder bumps the version number, never Claude Code acting on its own.

### Consistency Checks: SQL First

Enforce invariants at the SQL level whenever possible;
fall back to Python only when SQL cannot express the check or cannot repair a violation:

- A **single-row invariant** (what a column may hold given the other columns in the same row)
  is a `CHECK` constraint on the table,
  e.g. the `step` table's `CHECK (NOT deferred OR state = PENDING)`.
- An invariant spanning **multiple rows** (e.g. a node's creator must have a compatible `kind`)
  cannot be a `CHECK` constraint,
  because SQLite does not re-evaluate a `CHECK` when a *different* row changes.
  Use a `RAISE(ABORT, ...)` trigger instead (see [Triggers](#triggers) below).
- A **graph-wide** invariant (e.g. every attached node must be reachable from the root via
  creator edges) can require recursing over an unbounded number of rows,
  which a trigger cannot do either,
  since a trigger only sees the row(s) touched by the statement that fired it.
  Check these in Python via a recursive SQL query,
  run once at startup after opening an existing database (`Trellis._check_consistency()`),
  not on every mutation:
  since every write already goes through the CHECK/trigger-guarded path,
  a startup-only pass is enough to catch whatever a crash could have left behind.
  These checks raise `ConsistencyError`, not `GraphError`:
  no plan can reach them through the public API, so they report a bug in StepUp,
  and unlike a `GraphError` (a `UsageError`) they keep their full traceback on the client.
- A startup check that also **repairs** what it finds
  (e.g. `Workflow._check_consistency()` marking a succeeded step with a non-`BUILT` output
  back to `PENDING`) belongs in Python regardless,
  since a `CHECK`/trigger can only reject a write, not fix one up.
- Remove a Python-side check once a `CHECK` constraint or trigger already covers the same write
  path, because it can no longer fire, so keeping it "for safety" just adds dead code
  (e.g. the file-hash-missing check dropped from `Workflow._check_consistency()` once the
  `file` table's own `CHECK (state NOT IN (...) OR hash IS NOT NULL)` made it unreachable).

### Unreachable Branches

A branch that no caller can currently reach is dead code, and what to do with it depends on
what reaching it would mean:

- Reaching it would imply **a bug in StepUp**: keep the branch,
  but let it `raise ConsistencyError` instead of returning a plausible result.
  A fallback that quietly does something sensible lets the bug travel;
  a `ConsistencyError` stops it where it starts and keeps its traceback.
  Use `ConsistencyError`, not a bare `RuntimeError`:
  it is what marks an error as a bug in StepUp rather than in the user's plan.
  Examples in `workflow.py`: `_creator_phrase` on a creator kind it has no phrase for,
  and `_file_collision_message` on two identical declarations,
  which its callers must already have skipped as a no-op.
- Reaching it would be **a case the callers happen not to produce**,
  handled correctly if they ever did: delete it.
  Whoever needs it later adds it back with the call site and the test that reaches it.

Do not keep a branch alive with a comment that it may become reachable again:
such a comment records a past state of the code, which the code no longer supports
and nothing keeps true.

### Triggers

Invariant-preserving side effects (derived-column bookkeeping that would otherwise require
a Python read-branch-write round trip on every mutation) are implemented as
`AFTER INSERT/UPDATE/DELETE` triggers, colocated with the table they read from inside that
node class's `*_SCHEMA` string.
Triggers are also used for pure validation (`RAISE(ABORT, ...)`) of multi-row invariants that a
`CHECK` constraint cannot express,
e.g. `node_check_creator_kind_ins`/`_upd` and `dependency_check_kinds_ins` (`WORKFLOW_SCHEMA`,
`workflow.py`), which replaced the Python-side `Workflow._check_creator`/`_check_source` hooks.
Which table such a trigger is placed on is part of the invariant, not a free choice:
`file_check_undeclared_detached_ins`/`_upd` (`FILE_SCHEMA`, `file.py`) guards
"`state = UNDECLARED` implies the node is detached" from the `file` side only,
because the equivalent trigger on `node` would abort on the legitimate window in which
`Trellis.create` re-attaches a recycled node before its new state is written.
Trigger names follow the same `<table>_<purpose>` convention as indexes, with no prefix.
`WHEN` clauses that depend on enum values are generated via f-string interpolation against
the enum (e.g. `{StepState.SUCCEEDED.value}`) rather than hardcoded literals,
so they can never drift from `enums.py`.

## Directory Creation and Removal

StepUp has no node type for directories, yet it creates and removes the ones its steps need.
The two halves are deliberately symmetric:
**a directory is created when a step is about to use it,
and marked for removal when that step or its files leave the workflow.**

### Creation

`Executor._run_command` creates the working directory of a step
and the parent directories of its regular and volatile outputs,
right before launching the command.
The `amend_step` RPC does the same for outputs declared while the step is already running.
`Workflow.create_dirs` is the only place that calls `makedirs_p`.

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
`File.before_delete` marks the file's parent directory
and `Step.before_delete` marks the step's working directory.
`revert_optional_steps` builds a queue of its own and returns it,
because the nodes of the outputs it flags stay in the graph
and therefore never reach `before_delete`.
`Builder.finalize` merges the two queues and hands them to `remove_deletable_files`.
A directory is marked whatever the state of the file that named it,
which is what catches an output the user removed by hand:
the file itself is then no longer in `to_be_deleted`, but its directory still is.

`remove_deletable_files` deletes the files first and hands the directories to
`_prune_empty_dirs`, which removes each one only if it is empty,
and then walks up to the root doing the same.
Emptiness is the only safeguard here,
so marking a directory too eagerly is harmless by construction.
The one exception is the project root and its ancestors,
which `mark_dir_to_be_deleted` refuses to queue and the upward walk stops at:
a step may build outside the root, but the directories above it are not StepUp's to remove.

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
after changing this class.

Two properties of `_NtPath` are easy to break:

- `os.fspath()` returns an instance of a `str` subclass unchanged,
  so `pathlib` and `ntpath` call `str` methods on the instance itself.
  `path.Path` overrides `replace`, `__add__` and `__radd__` to return a new path,
  which passes through the normalizing constructor.
  `_NtPath.replace` therefore builds its result without normalization,
  or `p.replace("/", "\\")` would return `p`, and `pathlib.Path(p)` would be parsed incorrectly.
  For the same reason, the `ntpath` variant converts its arguments to plain strings.
- Methods of `path.Path` compare results of the path module with parts of instances
  (e.g. `relpathto` with `normcase`),
  so the module and the constructor must produce the same spelling.

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

- The director imports `api.py` indirectly, through `executor.py` and `run.py`,
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
because other core modules (`extapi.py`, `interact.py`) import them.
An extension talks to the director through the API functions, not through the RPC client.
