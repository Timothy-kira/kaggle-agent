#!/usr/bin/env python3
"""Prove the MCP server can be located and actually served, in the local AND Marketplace layouts.

A local install is ``<root>/kaggle-agent``. A Marketplace install is cached under a directory
named after its content hash, further down and under a name no plugin can predict:
``<root>/plugin-cache/official/sha256-tree-<hash>``. A search that only descends one level
finds the first and silently misses the second, and the symptom on the user's machine is an
install that reports no tools at all - which reads like "the plugin did not register" and sends
the reader to the wrong place entirely.

Two things are checked, and neither substitutes for the other:

* :func:`agent_server.locate` finds the package, and refuses a directory that merely holds a
  file of the same name.
* The bootstrap in ``servers.mcp.json`` - a SECOND, hand-written implementation of the same
  search, because the manifest cannot carry a reliable path - actually starts a server that
  answers ``initialize``. Driving it end to end is the point: a search that finds the package and
  then fails to run it is the failure mode this suite exists to catch, and it is invisible to any
  check that stops at "the right directory came back".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import agent_server  # noqa: E402

FAKE_MANIFEST = json.dumps({"name": "kaggle-agent", "version": "0.0.0"}, indent=2)
FOREIGN_MANIFEST = json.dumps({"name": "some-other-plugin", "version": "0.0.0"}, indent=2)

FIXTURE_SERVER = '''\
"""A stand-in for kaggle_server.py: the smallest thing that answers like an MCP server."""
import json
import sys

REPLIES = {
    "initialize": {"protocolVersion": "2024-11-05",
                   "serverInfo": {"name": "fixture", "version": "0.0.0"}},
    "tools/list": {"tools": []},
}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    reply = REPLIES.get(message.get("method"))
    if reply is not None:
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": message.get("id"), "result": reply}) + "\\n")
        sys.stdout.flush()
'''

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def make_package(parent: Path, *, manifest: str = FAKE_MANIFEST) -> Path:
    """A package directory carrying this plugin's manifest and the modules the entry runs."""
    (parent / ".minimax-plugin").mkdir(parents=True, exist_ok=True)
    (parent / ".minimax-plugin" / "plugin.json").write_text(manifest, encoding="utf-8")
    (parent / "mcp").mkdir(parents=True, exist_ok=True)
    (parent / "mcp" / "kaggle_server.py").write_text(FIXTURE_SERVER, encoding="utf-8")
    shutil.copyfile(ROOT / "mcp" / "agent_server.py", parent / "mcp" / "agent_server.py")
    return parent


def roots_under(tmp: Path) -> dict[str, Path]:
    return {
        "local": tmp / "home-local" / ".minimax" / "plugins",
        "market": tmp / "home-market" / ".minimax" / "v2" / "plugin-cache",
    }


def with_roots(roots: dict[str, Path], body):
    saved = agent_server.PLUGIN_ROOTS
    agent_server.PLUGIN_ROOTS = tuple(str(p) for p in roots.values())
    try:
        return body()
    finally:
        agent_server.PLUGIN_ROOTS = saved


# ------------------------------------------------------------------ locate()
def test_locate_layouts(tmp: Path) -> None:
    print("locate(): both install shapes")
    roots = roots_under(tmp)

    local_pkg = make_package(roots["local"] / "kaggle-agent")
    found = with_roots(roots, agent_server.locate)
    check(found == str(local_pkg / "mcp"), f"local install found ({found})")

    shutil.rmtree(roots["local"])
    market_pkg = make_package(roots["market"] / "official" / ("sha256-tree-v1-" + "ab" * 32))
    found = with_roots(roots, agent_server.locate)
    check(found == str(market_pkg / "mcp"), f"marketplace cache found ({found})")

    shutil.rmtree(roots["market"])
    # locate() falls back to the running package, which is real; the point is that it does not
    # return anything that was left in a fixture directory, and that nothing raises.
    found = with_roots(roots, agent_server.locate)
    check(
        found is None or str(tmp) not in found,
        f"empty roots -> nothing invented from a fixture ({found})",
    )


def test_locate_rejects_foreign_manifest(tmp: Path) -> None:
    print("locate(): a directory is not ours because of its file names")
    roots = roots_under(tmp)
    make_package(roots["local"] / "kaggle-agent", manifest=FOREIGN_MANIFEST)
    found = with_roots(roots, agent_server.locate)
    check(
        found is None or str(tmp) not in found,
        "same filenames, different plugin name -> refused",
    )

    # And a manifest that is not readable JSON is declined rather than raised.
    broken = roots["local"] / "kaggle-agent"
    (broken / ".minimax-plugin" / "plugin.json").write_text("{ not json", encoding="utf-8")
    found = with_roots(roots, agent_server.locate)
    check(
        found is None or str(tmp) not in found,
        "unparseable manifest -> declined, search continues",
    )


# ------------------------------------------------------------------ the bootstrap, end to end
def bootstrap_source() -> str:
    cfg = json.loads((ROOT / "servers.mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["kaggle"]["args"]
    return args[args.index("-c") + 1]


def ask_the_server(home: Path, cwd: Path, method: str) -> tuple[dict | None, str]:
    """Start the server through servers.mcp.json and send one JSON-RPC call to it."""
    env = dict(os.environ)
    env.pop("PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # os.path.expanduser reads USERPROFILE on Windows
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", bootstrap_source()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(cwd),
    )
    result: dict | None = None
    collected: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        collected.append(proc.stderr.read() or "")

    watcher = threading.Thread(target=drain, daemon=True)
    watcher.start()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                     "params": {"protocolVersion": "2024-11-05"}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        result = json.loads(line) if line.strip() else None
    except (BrokenPipeError, ValueError):
        result = None
    finally:
        try:
            proc.stdin and proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        watcher.join(timeout=5)
    return result, "".join(collected)


def test_bootstrap_end_to_end(tmp: Path) -> None:
    print("servers.mcp.json bootstrap: the hand-written second copy, driven end to end")
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)

    # 1. the Marketplace shape, in a home that holds nothing else
    home = tmp / "home-boot-market"
    make_package(home / ".minimax" / "v2" / "plugin-cache" / "official"
                 / ("sha256-tree-v1-" + "cd" * 32))
    reply, err = ask_the_server(home, elsewhere, "initialize")
    info = ((reply or {}).get("result") or {}).get("serverInfo") or {}
    check(info.get("name") == "fixture",
          f"marketplace cache -> server answers initialize ({info or (err or '')[:140]!r})")

    # 2. the local shape
    home = tmp / "home-boot-local"
    make_package(home / ".minimax" / "plugins" / "kaggle-agent")
    reply, err = ask_the_server(home, elsewhere, "initialize")
    info = ((reply or {}).get("result") or {}).get("serverInfo") or {}
    check(info.get("name") == "fixture",
          f"local install -> server answers initialize ({info or (err or '')[:140]!r})")

    # 3. a second call on the same connection, so this is a server and not a one-shot script
    reply, err = ask_the_server(home, elsewhere, "tools/list")
    check("result" in (reply or {}), f"tools/list answered ({reply or (err or '')[:140]})")

    # 4. the counter-example: an unrelated plugin with the same filenames, alone in its home.
    # If the bootstrap loaded it as this plugin, a user would get somebody else's server.
    home = tmp / "home-boot-foreign"
    make_package(home / ".minimax" / "v2" / "plugin-cache" / "official"
                 / ("sha256-tree-v1-" + "ef" * 32), manifest=FOREIGN_MANIFEST)
    reply, err = ask_the_server(home, elsewhere, "initialize")
    check(reply is None and "cannot locate" in err,
          f"foreign manifest alone -> refuses rather than serving it ({(err or '')[:120]!r})")

    source = bootstrap_source()
    check("plugin-cache" in source and "plugin-import" in source,
          "bootstrap candidate roots include the marketplace cache")
    check("os.path.join(b,n,m)" in source, "bootstrap descends two levels")


def test_this_machine() -> None:
    print("this machine")
    found = agent_server.locate()
    check(
        found is not None and os.path.isfile(os.path.join(found, "kaggle_server.py")),
        f"the installed package is still reachable ({found})",
    )
    check(found == str(ROOT / "mcp"), "and it is this working tree, as before the change")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ka-marketplace-"))
    try:
        test_locate_layouts(tmp)
        test_locate_rejects_foreign_manifest(tmp)
        test_bootstrap_end_to_end(tmp)
        test_this_machine()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"\nPROBE_MARKETPLACE_FAILED ({len(failures)})")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nPROBE_MARKETPLACE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
