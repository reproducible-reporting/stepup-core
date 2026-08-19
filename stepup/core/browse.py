# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Web browser interface to StepUp's build graph."""

import argparse
import contextlib
import html
import importlib.resources
import json
import stat
import threading
import traceback
import webbrowser
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jinja2

from .cattrs import json_converter
from .config import ConfigLoader
from .enums import FileState, Need, StepState
from .hash import FileHash, StepHash, fmt_digest, fmt_env_value
from .nglob import NamedGlob
from .sqlite3 import connect
from .step import Step
from .tool import SubParsers, ToolFunc, get_graph_db_path
from .utils import escape_command_display, format_subprocess, positive_int

__all__ = ("add_browse_subcommand",)


def _detect_browsers() -> str:
    """Return a comma-separated list of browsers detected by the `webbrowser` module."""
    with contextlib.suppress(webbrowser.Error):
        webbrowser.get()
    return ", ".join(webbrowser._tryorder) if webbrowser._tryorder else "none detected"


def add_browse_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Define command-line arguments for the browse tool.

    Parameters
    ----------
    subparsers
        The subparser to add the browse tool to.
    loader
        The configuration loader to override the default configuration with config file values.

    Returns
    -------
    tool_func
        The function to call with the parsed args to execute the browse command.
    """
    parser = subparsers.add_parser("browse", help="Browse the StepUp build graph.")
    parser.add_argument(
        "--port",
        type=positive_int,
        default=7837,
        help="Port to bind the server to (default: %(default)s).",
    )
    parser.add_argument(
        "--open-browser",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Open a web browser to view the graph (default: %(default)s).",
    )
    parser.add_argument(
        "--browser",
        type=str,
        default=None,
        help=f"Browser to use (default: system default). Detected browsers: {_detect_browsers()}.",
    )
    loader.patch_parser(parser)
    return browse_tool


def browse_tool(args: argparse.Namespace) -> None:
    """Launch a web server to browse the build graph and print the URL to the console."""
    # Ugly hack to pass the database path to the request handler.
    GraphServer.path_db = get_graph_db_path()

    # The server is started in a thread because a foreground browser would otherwise block the
    # server from answering its own requests.
    # (See below for more details.)
    server = HTTPServer(("localhost", args.port), GraphServer)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        url = f"http://localhost:{args.port}"
        print(f"Server started {url}")
        print("Press Ctrl+C to stop the server.")

        # Launch a browser
        if args.open_browser and args.browser is None:
            print("Set --browser or the BROWSER environment variable to pick a browser.")
        blocking = False
        if args.open_browser:
            try:
                browser = webbrowser.get(args.browser)
            except webbrowser.Error:
                browser = None
            # Generic (non-background) browsers, e.g. text-mode ones like lynx or w3m,
            # run in the foreground and block browser.open() below until the user quits them.
            # In that case, exit as soon as they return instead of waiting for Ctrl+C.
            # GUI browsers return immediately after launching, so keep serving until interrupted.
            blocking = isinstance(browser, webbrowser.GenericBrowser) and not isinstance(
                browser, webbrowser.BackgroundBrowser
            )
            if browser is None or not browser.open(url):
                print("Warning: could not open a browser. Open the URL above manually.")
                blocking = False
        if not blocking:
            with contextlib.suppress(KeyboardInterrupt):
                server_thread.join()
    finally:
        server.shutdown()
        if GraphServer.con is not None:
            GraphServer.con.close()
        server.server_close()


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <link rel="icon" href="/logo.svg">
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>StepUp Graph Browser</title>
  <style>
    :root {
      --main-font: IBM Plex Sans, Arial, sans-serif;
      --background-color: #eeeeee;
      --pre-color: #dddddd;
      --heading-color: #222222;
      --text-color: #000000;
      --link-color: #444444;
      --blue: #0077cc;
      --green: #009900;
      --red: #cc0000;
      --orange: #dd7700;
      --purple: #aa00dd;
      --yellow: #bbaa00;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --background-color: #181818;
        --pre-color: #333333;
        --heading-color: #cccccc;
        --text-color: #ffffff;
        --link-color: #aaaaaa;
        --blue: #0099ff;
        --green: #00cc00;
        --red: #ff0000;
        --orange: #ff8800;
        --purple: #dd00ff;
        --yellow: #ffdd00;
      }
    }
    body {
      font-family: var(--main-font);
      margin: 0;
      padding: 15px;
      color: var(--text-color);
      background-color: var(--background-color);
    }
    h1, h2, h3 {
      color: var(--heading-color);
      margin-bottom: 0px;
      margin-top: 8px;
    }
    hr {
      border: 1px solid var(--heading-color);
    }
    .indent {
      margin-left: 10px;
    }
    p {
      margin-left: 10px;
      margin-top: 5px;
      margin-bottom: 5px;
    }
    ul, ol {
      margin-top: 5px;
      margin-bottom: 5px;
    }
    pre {
      background-color: var(--pre-color);
      margin-left: 10px;
      margin-top: 5px;
      margin-bottom: 5px;
      padding: 10px;
      border-radius: 4px;
    }
    code {
      background-color: var(--pre-color);
      padding: 1px 3px;
      border-radius: 4px;
    }
    a {
      color: var(--link-color);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    a#reload {
      font-size: 1em;
      border-radius: 4px;
      margin: 5px;
      padding: 3px 8px 3px 8px;
      color: var(--background-color);
      border: 0px;
      background-color: var(--link-color);
    }
    header {
    }
    main {
    }
    footer {
    }
    .alert {
      color: red;
      font-weight: bold;
    }
    input {
      font-family: var(--main-font);
      font-size: 1em;
      border-radius: 4px;
      margin: 5px;
      padding: 10px;
      cursor: pointer;
    }
    input[type=text] {
      box-sizing: border-box;
      color: var(--text-color);
      border: var(--heading-color) solid 1px;
      background-color: var(--background-color);
    }
    input[type=submit] {
      padding: 10px 20px 10px 20px;
      color: var(--background-color);
      border: 0px;
      background-color: var(--link-color);
    }
    .undeclared { color: var(--text-color); }
    .unconfirmed { color: var(--purple); }
    .missing { color: var(--red); }
    .confirmed { color: var(--blue); }
    .planned { color: var(--orange); }
    .built { color: var(--green); }
    .outdated { color: var(--yellow); }
    .volatile { color: var(--purple); }
    .pending { color: var(--orange); }
    .queued { color: var(--yellow); }
    .running { color: var(--blue); }
    .succeeded { color: var(--green); }
    .failed { color: var(--red); }
    .yes { color: var(--green); }
    .no { color: var(--red); }
    .deferred { color: var(--orange); }
    .clean { color: var(--green); }
    .required { color: var(--blue); }
    table.nglob, table.hashes {
      margin: 12px;
    }
    table.nglob, table.nglob tr, table.nglob tr td, table.nglob tr th,
    table.hashes, table.hashes tr, table.hashes tr td, table.hashes tr th {
      border: 1px solid var(--link-color);
      border-collapse: collapse;
    }
    table.nglob tr td, table.nglob tr th,
    table.hashes tr td, table.hashes tr th {
      vertical-align: top;
      text-align: left;
      padding: 1px 4px 4px 4px;
    }
    table.edges tr td {
      vertical-align: top;
      padding-top: 2px;
      padding-bottom: 2px;
    }
    table.edges tr td:nth-child(1) {
      text-align: right;
      padding-right: 10px;
      width: 15ex;
    }
    table.edges tr td:nth-child(2) {
      padding-right: 10px;
      padding-left: 10px;
    }
    table.edges tr {
      margin: 4px;
    }
  </style>
</head>
<body>
  <header>
    <a href="/"><h1>StepUp Graph Browser</h1></a>
    <p>{{ path_db }} <a id="reload" href="{{ reload_url }}">↻</a></p>
    <hr>
  </header>
  <main>
    {{ main }}
  </main>
</body>
</html>"""


MAIN_TEMPLATE = """\
<h2>Overview</h2>
<p>The graph contains:<ul>
  <li><a href="/search_file/">{{ n_files }} files</a></li>
  <li><a href="/search_step/">{{ n_steps }} steps</a></li>
  <li><a href="/search_st/">{{ n_static_trees }} static trees</a></li>
</ul></p>
<p>Entry point: {{ a_entry }}</p>
<h2>Search</h2>
<p><form action="/search/" method="get">
  <table>
    <tr>
      <td><label for="pattern"><b>Pattern:</b></label></td>
      <td><input type="text" id="pattern" name="pattern" style="width: 500px;"></td>
    </tr>
    <tr>
      <td></td>
      <td><input type="submit" value="(Any)">
      <input type="submit" value="🗏 File" formaction="/search_file/">
      <input type="submit" value="⚙ Step" formaction="/search_step/">
      <input type="submit" value="𐂷 Static Tree" formaction="/search_st/"></td>
    </tr>
  </table>
</form></p>
"""

KIND_SYMBOLS = {
    "root": "⌂",
    "file": "🗏",
    "step": "⚙",
    "st": "𐂷",
}

KIND_NAMES = {
    "root": "Root",
    "file": "File",
    "step": "Step",
    "st": "Static Tree",
}

STATE_SQL = """
(CASE node.kind
  WHEN 'file' THEN (SELECT state FROM file WHERE file.node = node.i)
  WHEN 'step' THEN (SELECT state FROM step WHERE step.node = node.i)
  ELSE NULL
END)
"""


class GraphServer(BaseHTTPRequestHandler):
    path_db = None
    con = None

    def log_message(self, fmt, *args):
        """Suppress the default request logging to stderr.

        Otherwise, BaseHTTPRequestHandler.log_message would clutter the terminal
        in which a text-mode browser (e.g. lynx) shows the graph.
        """

    def do_GET(self):
        # Basic URL parsing.
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)

        # (Re)load the database if requested, always in read-only mode.
        if "reload" in args or self.con is None:
            if self.con is not None:
                self.con.close()
            self.con = connect(self.path_db, read_only=True)
        args.pop("reload", None)

        # Prepare the Jinja2 environment.
        env_kwargs = {
            "keep_trailing_newline": True,
            "trim_blocks": True,
            "undefined": jinja2.StrictUndefined,
            "autoescape": False,
        }
        env = jinja2.Environment(**env_kwargs)

        response_code = 200

        if parsed.path == "/logo.svg":
            self.send_response(200)
            self.send_header("Content-type", "image/svg+xml")
            self.end_headers()
            data_svg = importlib.resources.files("stepup.core").joinpath("logo.svg").read_bytes()
            self.wfile.write(data_svg)
            return

        try:
            if parsed.path == "/":
                main = self._main(env)
            elif parsed.path.startswith("/node/"):
                main = self._node(env, args)
            elif parsed.path.startswith("/search/"):
                main = self._search(env, args)
            elif parsed.path.startswith("/search_file/"):
                main = self._search_file(env, args)
            elif parsed.path.startswith("/search_step/"):
                main = self._search_step(env, args)
            elif parsed.path.startswith("/search_st/"):
                main = self._search_st(env, args)
            else:
                main = self._not_found(env)
                response_code = 404
            main = "\n".join(main)
        except Exception as exc:  # noqa: BLE001
            main = "\n".join(self._error(env, exc))
            response_code = 500

        self.send_response(response_code)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        # Put everything in the HTML template with the standard header.
        template = env.from_string(HTML_TEMPLATE)
        template.filename = "<HTML_TEMPLATE>"
        args["reload"] = ["1"]
        reload_url = parsed._replace(query=urlencode(args, doseq=True)).geturl()
        html = template.render(path_db=self.path_db, main=main, reload_url=reload_url)
        self.wfile.write(html.encode("utf-8"))

    # --- main page and subpages ---

    def _main(self, env) -> Iterator[str]:
        # Get a few stats from the database.
        (n_files,) = self.con.execute("SELECT COUNT(*) FROM file").fetchone()
        (n_steps,) = self.con.execute("SELECT COUNT(*) FROM step").fetchone()
        (n_static_trees,) = self.con.execute("SELECT COUNT(*) FROM node WHERE kind='st'").fetchone()

        # Get the top-level plan.py step.
        label_entry = Step.adjust_label("./plan.py")
        (i_entry,) = self.con.execute(
            "SELECT i FROM node WHERE kind='step' AND label = ?", (label_entry,)
        ).fetchone()
        a_entry = self._format_node(i_entry, "step", label_entry, False)

        # Format main HTML content.
        template = env.from_string(MAIN_TEMPLATE)
        template.filename = "<MAIN_TEMPLATE>"
        yield template.render(
            n_files=n_files, n_steps=n_steps, n_static_trees=n_static_trees, a_entry=a_entry
        )

    def _node(self, env, args: dict[str, list[str]]) -> Iterator[str]:
        if "i" not in args or len(args["i"]) != 1:
            raise ValueError("Node ID 'i' must be provided exactly once.")
        if not args["i"][0].isdigit():
            raise ValueError("Node ID 'i' must be an integer.")
        node_i = int(args["i"][0])
        (kind, label, creator_i, detached) = self.con.execute(
            "SELECT kind, label, creator, detached FROM node WHERE i = ?", (node_i,)
        ).fetchone()
        node_prefix = "Detached " if detached else ""
        yield f"<h2>{node_prefix}Node {node_i}</h2>"
        kind_name = KIND_NAMES.get(kind, kind)
        if kind != "root":
            kind_name = f"<a href='/search_{kind}/'>{kind_name}</a>"
        yield f"<p><b>Kind:</b> {kind_name}</p>"
        display_label = escape_command_display(label) if kind == "step" else label
        yield f"<p><b>Label:</b> {html.escape(display_label)}</p>"

        # Format the state, which exists only for files and steps.
        if kind == "step":
            sql_props = (
                "SELECT state, need, duration, deferred, defer_count, shell, env_overrides, "
                "_safe, _check_safe, _holding, _implied_need, _tail_time, _check_after "
                "FROM step WHERE node = ?"
            )
            (
                state_i,
                need_id,
                duration,
                deferred,
                defer_count,
                shell,
                env_overrides,
                safe,
                check_safe,
                holding,
                implied_need_id,
                tail_time,
                check_after,
            ) = self.con.execute(sql_props, (node_i,)).fetchone()
            state = StepState(state_i)
            yield f"<p><b>Runs in shell:</b> {'yes' if shell else 'no'}</p>"
            yield f'<p><b>State:</b> <span class="{state.name.lower()}">{state.name}</span></p>'
            if deferred:
                yield ('<p><b>Deferred:</b> <span class="deferred">yes</span></p>')
            if defer_count > 0:
                yield f"<p><b>Defer count:</b> {defer_count}</p>"
            need = Need(need_id)
            implied_need = Need(implied_need_id)
            if detached:
                yield (
                    f"<p><b>Need:</b> {need.name} "
                    "(implied need not propagated because it is detached)</p>"
                )
            elif need == implied_need:
                yield f"<p><b>Need:</b> {need.name}</p>"
            else:
                yield f"<p><b>Need:</b> {implied_need.name} (implied by sinks > {need.name})</p>"
            yield f"<p><b>Duration:</b> {duration:.2f} s</p>"
            if detached:
                yield "<p><b>Tail time:</b> not applicable to detached steps</p>"
            else:
                yield (
                    f"<p><b>Tail time:</b> {tail_time:.2f} s "
                    "(longest wall-time path to any terminal node, including its own duration)</p>"
                )
            if not safe:
                yield (
                    "<p><b>This step is not safe to run:</b> "
                    "a creator does not have state RUNNING or SUCCEEDED.</p>"
                )
            if check_safe:
                yield (
                    "<p><b>The state of this step has not been propagated to the <code>safe</code> "
                    "field of its products yet.</b></p>"
                )
            if check_after:
                yield (
                    "<p><b>The need and duration of this step have not been propagated to the "
                    "<code>_implied_need</code> and <code>_tail_time</code> fields of this step "
                    "and its sources yet.</b></p>"
                )
            if holding:
                yield (
                    "<p><b>This step is holding:</b> "
                    "its descendant steps are not safe to run "
                    "until it calls <code>release()</code>.</p>"
                )

            sql_env = "SELECT name, dynamic FROM env_var WHERE node = ?"
            env_deps = list(self.con.execute(sql_env, (node_i,)))
            if len(env_deps) > 0:
                yield "<h3>Uses Environment Variables</h3>"
                for env_var, dynamic in env_deps:
                    line = f"<p>{env_var}"
                    if dynamic:
                        line += " [dynamic]"
                    line += "</p>"
                    yield line

            if env_overrides is not None:
                env_overrides = json.loads(env_overrides)
                yield "<h3>Overrides Environment Variables</h3>"
                block = "\n".join(f"{name}={value}" for name, value in env_overrides.items())
                yield f"<pre>{block}</pre>"

            sql_res = "SELECT name, units FROM step_resource WHERE node = ? ORDER BY name"
            resources = list(self.con.execute(sql_res, (node_i,)))
            if len(resources) > 0:
                yield "<h3>Required Resources</h3>"
                for res_name, res_units in resources:
                    yield f"<p><b>{res_name}:</b> {res_units}</p>"

            sql_nglob = "SELECT data FROM nglob WHERE node = ?"
            nglob_rows = list(self.con.execute(sql_nglob, (node_i,)))
            if len(nglob_rows) > 0:
                yield "<h3>Defines (Named) Globs</h3>"
                for nglob_row in nglob_rows:
                    ng = json_converter.structure(json.loads(nglob_row[0]), NamedGlob)
                    yield '<table class="nglob">'
                    yield "<tr>"
                    yield f"<th><code>{ng.pattern}</code></th>"
                    subs_keys = []
                    for key, value in ng.subs.items():
                        subs_keys.append(key)
                        yield f"<th><code>{key} = {value}</code></th>"
                    yield "</tr>"
                    for match in ng.matches():
                        yield "<tr>"
                        files = match.files
                        if isinstance(files, str):
                            yield f"<td><code>{files}</code></td>"
                        else:
                            yield "<td>"
                            yield "</br>".join(f"<code>{path}</code>" for path in files)
                            yield "</td>"
                        for key in subs_keys:
                            yield f"<td><code>{match.mapping.get(key, '?')}</code></td>"
                        yield "</tr>"
                    yield "</table>"

            yield from self._format_step_hash(node_i)

            sql_outcome = (
                "SELECT returncode, stdout, stderr, utime, stime, wtime "
                "FROM step_outcome WHERE node = ?"
            )
            row = self.con.execute(sql_outcome, (node_i,)).fetchone()
            if row is not None:
                yield "<h3>Child Outcome</h3>"
                yield f"<p><b>Return Code:</b> {row[0]}</p>"
                if row[1] != "":
                    yield "<p><b>Standard Output</b></p>"
                    yield f"<pre>{html.escape(row[1])}</pre>"
                if row[2] != "":
                    yield "<p><b>Standard Error</b></p>"
                    yield f"<pre>{html.escape(row[2])}</pre>"
                yield "<p><b>Resource Usage</b></p><ul>"
                yield f"<li>User CPU Time: {row[3]:.3f} s</li>"
                yield f"<li>System CPU Time: {row[4]:.3f} s</li>"
                yield f"<li>Wall Clock Time: {row[5]:.3f} s</li>"
                yield "</ul>"

            sql_sub = (
                "SELECT cmd, workdir, env_overrides, returncode, shell, stdin, stdout, stderr "
                "FROM step_subprocess WHERE node = ? ORDER BY rowid"
            )
            subs = list(self.con.execute(sql_sub, (node_i,)))
            if len(subs) > 0:
                yield "<h3>Subprocesses</h3>"
                for (
                    cmd,
                    workdir,
                    env_overrides,
                    returncode,
                    shell_int,
                    stdin,
                    stdout,
                    stderr,
                ) in subs:
                    line = format_subprocess(
                        cmd,
                        workdir,
                        None if env_overrides is None else json.loads(env_overrides),
                        returncode,
                        shell=bool(shell_int),
                    )
                    yield f"<pre>{html.escape(line)}</pre>"
                    if stdin != "":
                        yield '<div class="indent"><p>stdin:</p>'
                        yield f"<pre>{html.escape(stdin)}</pre></div>"
                    if stdout != "":
                        yield '<div class="indent"><p>stdout:</p>'
                        yield f"<pre>{html.escape(stdout)}</pre></div>"
                    if stderr != "":
                        yield '<div class="indent"><p>stderr:</p>'
                        yield f"<pre>{html.escape(stderr)}</pre></div>"

        elif kind == "file":
            (state_i, hash_value) = self.con.execute(
                "SELECT state, hash FROM file WHERE node = ?",
                (node_i,),
            ).fetchone()
            state = FileState(state_i)
            file_hash = FileHash.from_json(hash_value)
            yield f'<p><b>State:</b> <span class="{state.name.lower()}">{state.name}</span></p>'
            yield f"<p><b>Digest:</b> {fmt_digest(file_hash.digest)}</p>"
            if len(file_hash.digest) > 1:
                yield f"<p><b>Mode:</b> {stat.filemode(file_hash.mode)}</p>"
                yield (
                    "<p><b>Modified:</b> "
                    f"{datetime.fromtimestamp(file_hash.mtime).strftime('%Y-%m-%d %H:%M:%S')}</p>"
                )
                yield f"<p><b>Size:</b> {file_hash.size}</p>"
                yield f"<p><b>Inode:</b> {file_hash.inode}</p>"

        # Format the creator.
        yield "<h3>Provenance Edges</h3>"
        creator = self.con.execute(
            f"SELECT kind, label, detached, {STATE_SQL} FROM node WHERE i = ?", (creator_i,)
        ).fetchone()
        if creator is not None:
            yield "<p>Creator</p>"
            yield '<table class="edges">'
            creator_kind, creator_label, creator_detached, creator_state_i = creator
            yield self._format_node(
                creator_i, creator_kind, creator_label, creator_detached, creator_state_i
            )
            yield "</table>"

        # Format the products.
        product_rows = self.con.execute(
            f"SELECT i, kind, label, {STATE_SQL} FROM node WHERE creator = ? ORDER BY kind, label",
            (node_i,),
        ).fetchall()
        if len(product_rows) > 0:
            yield "<p>Products</p>"
            yield '<table class="edges">'
            for prod_i, prod_kind, prod_label, state in product_rows:
                yield f"{self._format_node(prod_i, prod_kind, prod_label, False, state)}"
            yield "</table>"

        yield "<h3>Dependency Edges</h3>"

        # Format the sources.
        source_rows = self.con.execute(
            f"SELECT node.i, kind, label, dependency.i IN dynamic_dep, {STATE_SQL} FROM node "
            "JOIN dependency ON dependency.source = node.i "
            "WHERE dependency.sink = ? ORDER BY node.kind, node.label",
            (node_i,),
        ).fetchall()
        if len(source_rows) > 0:
            yield "<p>Sources</p>"
            yield '<table class="edges">'
            for sup_i, sup_kind, sup_label, dynamic, state in source_rows:
                yield self._format_node(sup_i, sup_kind, sup_label, False, state, dynamic)
            yield "</table>"

        # Format the sinks.
        sink_rows = self.con.execute(
            f"SELECT node.i, kind, label, dependency.i IN dynamic_dep, {STATE_SQL} FROM node "
            "JOIN dependency ON dependency.sink = node.i "
            "WHERE dependency.source = ? ORDER BY node.kind, node.label",
            (node_i,),
        ).fetchall()
        if len(sink_rows) > 0:
            yield "<p>Sinks</p>"
            yield '<table class="edges">'
            for cons_i, cons_kind, cons_label, dynamic, state in sink_rows:
                yield self._format_node(cons_i, cons_kind, cons_label, False, state, dynamic)
            yield "</table>"

    def _search(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern)

    def _search_file(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern, "file")

    def _search_step(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern, "step")

    def _search_st(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern, "st")

    def _search_low(self, _env, pattern: str, filter_kind: str | None = None) -> Iterator[str]:
        yield "<h2>Search Results</h2>"
        yield f"<p><b>Pattern:</b> {pattern}</p>"
        if filter_kind is None:
            cur = self.con.execute(
                f"SELECT i, kind, label, detached, {STATE_SQL} FROM node "
                "WHERE label GLOB ? ORDER BY kind, label",
                (f"*{pattern}*",),
            )
        else:
            cur = self.con.execute(
                f"SELECT i, kind, label, detached, {STATE_SQL} FROM node "
                "WHERE kind = ? AND label GLOB ? ORDER BY kind, label",
                (filter_kind, f"*{pattern}*"),
            )
        rows = list(cur)
        yield f"<p><b>Number of matches:</b> {len(rows)}</p>"
        yield '<table class="edges">'
        for i, kind, label, detached, state in rows:
            yield f"{self._format_node(i, kind, label, detached, state)}"
        yield "</table>"

    def _not_found(self, env) -> Iterator[str]:
        yield "<h2>404 Not Found</h2><p>The requested URL was not found on this server.</p>"

    def _error(self, env, exc) -> Iterator[str]:
        yield "<h2>500 Internal Server Error</h2>"
        yield "<pre>"
        yield traceback.format_exc()
        yield "</pre>"

    # --- helpers ---

    def _format_node(
        self,
        i: int,
        kind: str,
        label: str,
        detached: bool,
        state: int | None = None,
        dynamic: bool = False,
    ) -> str:
        sym = KIND_SYMBOLS.get(kind, f"?{kind}?")
        display_label = escape_command_display(label) if kind == "step" else label
        node_str = html.escape(display_label)
        if i is not None:
            node_str = f'<a href="/node/?i={i}">{node_str}</a>'
        if len(label) == 0:
            node_str = f"[{kind}]"
        if detached:
            node_str = f"({node_str})"
        if dynamic:
            node_str += " <i>[dynamic]</i>"
        if state is None:
            state_str = ""
        elif kind == "file":
            state_str = FileState(state).name
        elif kind == "step":
            state_str = StepState(state).name
        else:
            state_str = str(state)
        return (
            f'<tr><td class="{state_str.lower()}">{state_str}</td>'
            f"<td>{sym}</td><td>{node_str}</td></tr>"
        )

    def _format_step_hash(self, node_i: int) -> Iterator[str]:
        """Format the stored `StepHash` of a step, if any, as HTML."""
        row = self.con.execute("SELECT hash FROM step_hash WHERE node = ?", (node_i,)).fetchone()
        yield "<h3>Digest</h3>"
        if row is None:
            yield "<p>No digest stored for this step.</p>"
            return
        step_hash = StepHash.from_json(row[0])
        yield f"<p><b>Input Digest:</b> {fmt_digest(step_hash.inp_digest)}</p>"
        yield f"<p><b>Output Digest:</b> {fmt_digest(step_hash.out_digest)}</p>"

        inp_info = step_hash.inp_info
        if inp_info is not None:
            if len(inp_info.inp_hashes) > 0:
                yield "<h3>Digest: Input Files</h3>"
                yield from self._format_file_hash_table(inp_info.inp_hashes)
            if len(inp_info.env_values) > 0:
                yield "<h3>Digest: Input Environment Variables</h3>"
                for env_var, value in sorted(inp_info.env_values.items()):
                    yield f"<p><b>{html.escape(env_var)}:</b> {fmt_env_value(value)}</p>"
            if len(inp_info.env_overrides) > 0:
                yield "<h3>Digest: Input Environment Overrides</h3>"
                block = "\n".join(
                    f"{name}={value}" for name, value in sorted(inp_info.env_overrides.items())
                )
                yield f"<pre>{html.escape(block)}</pre>"

        out_info = step_hash.out_info
        if out_info is not None and len(out_info.out_hashes) > 0:
            yield "<h3>Digest: Output Files</h3>"
            yield from self._format_file_hash_table(out_info.out_hashes)

    def _format_file_hash_table(self, hashes: dict[str, FileHash]) -> Iterator[str]:
        """Format a path-to-`FileHash` mapping (as stored in a `StepHash`) as an HTML table."""
        yield '<table class="hashes">'
        yield (
            "<tr><th>Path</th><th>Digest</th><th>Mode</th>"
            "<th>Modified</th><th>Size</th><th>Inode</th></tr>"
        )
        for path in sorted(hashes):
            file_hash = hashes[path]
            yield f"<tr><td><code>{html.escape(path)}</code></td>"
            yield f"<td>{fmt_digest(file_hash.digest)}</td>"
            if file_hash.is_unknown:
                yield "<td>-</td><td>-</td><td>-</td><td>-</td></tr>"
            else:
                modified = datetime.fromtimestamp(file_hash.mtime).strftime("%Y-%m-%d %H:%M:%S")
                yield f"<td>{stat.filemode(file_hash.mode)}</td>"
                yield f"<td>{modified}</td>"
                yield f"<td>{file_hash.size}</td>"
                yield f"<td>{file_hash.inode}</td></tr>"
        yield "</table>"
