<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Launching Steps

This file documents `stepup/core/launch/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Step Launching and Interruption

This subpackage owns "run a step's command as a child process and return a `ChildOutcome`,"
independent of the step lifecycle in `director/executor.py`:

- `command.py`: command classification (built-in copy vs. shell vs. `*.py` script
  vs. console-script entry point vs. plain exec).
  `launch_command()` is the single dispatch entry point, called from `Executor._run_command()`.
- `subprocess.py`, `forkserver.py` and `copy.py`:
  spawning the subprocess or forkserver child, or copying a file without a process.
- `capture.py`: capturing the output, return code and resource usage of a child process.
- `worker.py`: `Run`, the state of a step while it is executed,
  and `Worker`, the base class for anything that is the in-flight work of a
  `Run` and can be interrupted by `Executor.interrupt()`: `SubprocessWorker` and
  `ForkserverWorker` stop the underlying OS process; `ThreadWorker`
  cancels a computation instead, so it overrides `interrupt()` directly
  rather than using `Worker`'s delivery template.

How a command becomes the arguments of `subprocess.Popen` is the business of
`platform/popen.py`, see `stepup/core/platform/CLAUDE.md`.

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
A `ThreadWorker` cancels its computation at every level,
and at `KILL` the director also stops waiting for its thread.
From `KILL` on, the `Executor` starts no new command or thread,
which would escape the stop that was just delivered.

### POSIX: Sessions and Process Groups

Every step runs in a **session of its own**: `start_new_session=True` for subprocesses and
`os.setsid()` at the top of `forkserver_entry` for forkserver children.
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
and put in a Job Object of its own (`platform.winapi.JobObject`) right after its creation.
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
  `Worker.suspend` and `resume` do nothing where `platform.oscompat.suspend_available()` is false.

### Built-in Copy

The command `cp -p SRC DST` without a shell, which is what `copy()` creates, starts no process:
`launch_command()` copies the file in a `ThreadWorker`.
Starting a process for every copy is slow without a forkserver, and Windows has no `cp`.
The command stays what it is on every platform,
and with it the step label, the step hash and the expected outputs of the examples.
The copy follows `cp -p`, not `shutil.copystat` (see `_copy_file`).
Like GNU `cp`, it tries to clone the whole file first, then `copy_file_range`
and last a read/write loop, and for a sparse source it only copies the data segments.
The last two check the cancel event between chunks, like the hashing in `hash.py`.

### Captured Output

`decode_output` turns the bytes that a step wrote into text:
it reads UTF-8 and drops what is invalid.
All captured output, also what a forkserver child collects as text,
passes through `normalize_newlines`, which turns `\r\n` into `\n` on every platform,
so that the recorded output of a step does not depend on where or how it ran.
