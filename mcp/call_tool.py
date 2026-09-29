"""Call one tool on this plugin's own MCP server, as an ordinary subprocess.

    python mcp/call_tool.py kaggle_competitions_list search=arc
    python mcp/call_tool.py --json @payload.json
    python mcp/call_tool.py --list

Payloads with quotes
--------------------
``--json`` takes a file path prefixed with ``@``, or a literal JSON string. The file form is
the one to reach for from a shell that eats quotes: PowerShell's native-argument conversion
strips the double quotes out of an inline JSON string, and the driver then reports a parse
error about a payload the caller can see is correct. ``key=value`` pairs are unaffected -
they carry no quotes - so a call with only those can always be typed directly.

Why this exists
---------------
A background subagent is not given this plugin's MCP tools. That is the documented behaviour,
and it was measured rather than assumed: a dispatched subagent's tool set contained no
``kaggle_*`` entry, ``tool_search`` was absent from its turn entirely, and ``mcp_invoke``
answered ``Unknown tool_name`` because a Host-bound tool needs a ``tool_ref`` that was not
injected. Writing "kaggle" in the prompt does not change any of that; a prompt is text.

What a subagent *does* have is ``bash`` and a filesystem. The server is a plain stdio process
speaking JSON-RPC, so it can be started by hand and driven. That was also measured, and this
file is that measurement with the fragile parts removed: a subagent should not have to
rediscover the manifest's argv, or work out that a Windows pipe cannot be ``select``-ed, or
guess whether the reply that never came was a hang or an encoding failure.

The route is deliberate, and it is a workaround rather than a supported path. What it buys is
the three tools the CLI cannot reach at all - ``kaggle_sources``, ``kaggle_experiment_tree``,
``kaggle_kernel_launch`` - which is what lets a delegated piece of work file its own evidence
instead of handing a report back for someone else to record. What it costs is a Python process
per call, and a dependency on a runtime limitation that may be closed one day. So this is
offered beside the ``kaggle`` CLI rather than instead of it: a caller that only needs to read
a leaderboard should use ``kaggle``, which is simpler and does not start a server.

Encoding is handled in binary on both pipes and decoded here, rather than left to a text-mode
stream. That is the 1.33.1 lesson applied at this end too: a text-mode pipe on Windows decodes
with the ANSI code page while the peer writes UTF-8, the payload arrives as mojibake, and the
symptom is a call that neither succeeds nor reports.

``KAGGLE_AGENT_HOME`` holds the account store as well as the trees
-------------------------------------------------------------------
Pointing ``KAGGLE_AGENT_HOME`` at a scratch directory to keep a probe out of the real trees
also takes the credentials with it, and every network-backed Kaggle tool then refuses with
"Kaggle is not signed in". That is not a bug and it is not a broken machine: the account store
lives in that same home. Read-only tools - the tree, the source store, the method index - keep
working against a scratch home; anything that talks to kaggle.com does not. A caller that needs
both should leave the home alone and clean up the competition it created instead.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 900  # seconds; kaggle_kernel_launch may legitimately spend this long

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_TOOL_ERROR = 3
EXIT_NO_REPLY = 4
EXIT_NO_SERVER = 1


def _fail(message: str, code: int) -> int:
    sys.stderr.write(f"[call_tool] {message}\n")
    return code


class _Server:
    """One MCP server process, driven over stdio.

    stdout is read on a thread because a Windows pipe cannot be waited on with ``select``, and
    stderr is drained on a second one for the same reason the server needs it: a child that
    fills its stderr pipe while nobody reads it blocks forever, and the failure that produces
    is a call that hangs rather than a call that reports.
    """

    def __init__(self, mcp_dir: str, timeout: int) -> None:
        package = os.path.dirname(mcp_dir)
        env = dict(os.environ)
        # The bootstrap treats PLUGIN_ROOT as its first search hint. Setting it pins this call
        # to the same copy the caller located, which matters on a machine that has both a local
        # install and a Marketplace cache.
        env["PLUGIN_ROOT"] = package
        self.proc = subprocess.Popen(
            [sys.executable, "-B", os.path.join(mcp_dir, "agent_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=package, env=env, bufsize=0,
        )
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.errs: "queue.Queue[str]" = queue.Queue()
        self.timeout = timeout
        for stream, sink in ((self.proc.stdout, self.lines), (self.proc.stderr, self.errs)):
            threading.Thread(target=self._drain, args=(stream, sink), daemon=True).start()

    @staticmethod
    def _drain(stream, sink) -> None:
        # Closing the pipes in close() ends this loop by raising rather than by returning
        # b"", and an exception on a daemon thread prints a traceback to the caller's stderr -
        # which is the very stream this script is trying to keep clean. Swallow it here.
        try:
            for raw in iter(stream.readline, b""):
                sink.put(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        sink.put(None)

    def _stderr(self) -> str:
        """Everything the server has said so far, drained without blocking."""
        lines = []
        while True:
            try:
                line = self.errs.get_nowait()
            except queue.Empty:
                break
            if line is None:
                break
            lines.append(line)
        return "".join(lines)

    def send(self, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload).encode("utf-8") + b"\n"
        self.proc.stdin.write(blob)
        self.proc.stdin.flush()

    def reply(self, want_id: int) -> dict[str, Any]:
        """The response carrying `want_id`, or a diagnostic explaining its absence.

        A response whose id does not match is not discarded silently: the server correlates on
        id because that is the only thing tying a reply to a request, and a mismatched one means
        something is wrong that a caller needs to see rather than have retried through.
        """
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                self.proc.poll()
                detail = (f"the server exited with {self.proc.returncode}"
                          if self.proc.poll() is not None else "the server's stdout closed")
                raise RuntimeError(f"{detail}. stderr: {self._stderr()[:400] or '(empty)'}")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == want_id:
                return msg
            if "error" in msg or "result" in msg:
                raise RuntimeError(f"the server answered id {msg.get('id')!r}, not {want_id}")
        raise RuntimeError(
            f"no reply to request {want_id} within {self.timeout}s. stderr: "
            f"{self._stderr()[:400] or '(empty)'}. A reply that never arrives is the same "
            f"shape as a payload the server could not read, so check the encoding before "
            f"concluding the tool is slow."
        )

    def close(self) -> None:
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _text_of(result: Any) -> str:
    """The human-readable part of a tool result, whether or not it is an error.

    The server already formats refusals as text with a code in it, so both paths print the same
    way; the difference is only the exit status, which is what a script branches on.
    """
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return "\n".join(str(c.get("text", "")) for c in result["content"]
                         if isinstance(c, dict)).strip()
    return json.dumps(result, ensure_ascii=False, indent=2)


def _parse(argv: list[str]) -> tuple[Optional[str], dict[str, Any], bool, int]:
    """`--list`, a `--json` payload, or `name key=value ...`."""
    if "--help" in argv or "-h" in argv:
        sys.stderr.write(__doc__ or "")
        raise SystemExit(EXIT_USAGE)
    timeout = DEFAULT_TIMEOUT
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = int(argv[i + 1])
        del argv[i:i + 2]
    if "--list" in argv:
        return None, {}, True, timeout
    if "--json" in argv:
        raw = argv[argv.index("--json") + 1]
        if raw.startswith("@"):
            # A file, because a shell that strips quotes turns a correct inline payload into a
            # parse error. Reading it here also means the caller never has to know that.
            with open(raw[1:], encoding="utf-8") as handle:
                raw = handle.read()
        payload = json.loads(raw)
        return str(payload["name"]), dict(payload.get("arguments") or {}), False, timeout
    if not argv:
        raise SystemExit(_fail("give a tool name, --json, or --list", EXIT_USAGE))
    args: dict[str, Any] = {}
    for item in argv[1:]:
        if "=" not in item:
            raise SystemExit(_fail(f"{item!r} is not key=value", EXIT_USAGE))
        key, value = item.split("=", 1)
        # A value that parses as JSON is passed as that type; anything else stays a string, so
        # `search=arc` and `page=2` need no ceremony and `rule={"a":1}` still works.
        try:
            args[key] = json.loads(value)
        except ValueError:
            args[key] = value
    return argv[0], args, False, timeout


def main(argv: list[str]) -> int:
    sys.path.insert(0, HERE)
    import agent_server

    try:
        name, args, want_list, timeout = _parse(argv)
    except json.JSONDecodeError as exc:
        return _fail(f"--json payload is not JSON: {exc}", EXIT_USAGE)
    except (ValueError, IndexError) as exc:
        return _fail(str(exc), EXIT_USAGE)

    mcp_dir = agent_server.locate()
    if not mcp_dir or not os.path.isfile(os.path.join(mcp_dir, agent_server.SERVER_MODULE)):
        return _fail(
            f"cannot locate {agent_server.SERVER_MODULE}. Searched {agent_server.PLUGIN_ROOTS} "
            f"to {agent_server.MAX_DEPTH} levels deep.", EXIT_NO_SERVER)

    server = _Server(mcp_dir, timeout)
    try:
        server.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": {"name": "call_tool", "version": "1"}}})
        server.reply(1)
        if want_list:
            server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            tools = server.reply(2).get("result", {}).get("tools", [])
            for tool in tools:
                sys.stdout.write(f"{tool.get('name')}\t{tool.get('description', '')[:90]}\n")
            return EXIT_OK
        server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": name, "arguments": args}})
        msg = server.reply(2)
    except RuntimeError as exc:
        return _fail(str(exc), EXIT_NO_REPLY)
    finally:
        server.close()

    if "error" in msg:
        return _fail(f"JSON-RPC error: {json.dumps(msg['error'], ensure_ascii=False)}",
                     EXIT_TOOL_ERROR)
    result = msg.get("result", {})
    sys.stdout.write(_text_of(result) + "\n")
    return EXIT_TOOL_ERROR if result.get("isError") else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
