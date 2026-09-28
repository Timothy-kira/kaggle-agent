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
  file of the same name. This is where identity lives: the manifest is parsed and its ``name``
  compared, so a directory cannot claim to be this plugin by having the right filenames.
* The bootstrap in ``servers.mcp.json`` - which has to exist because the manifest cannot carry
  a reliable path - actually starts a server that answers ``initialize``. Driving it end to end
  is the point: a search that finds the package and then fails to run it is invisible to any
  check that stops at "the right directory came back".

Everything above runs in a temporary HOME. That is what this suite used to do and nothing else,
and it is why the plugin could ship with a bootstrap that dies on every real machine: the
Desktop host launches the server with the user's profile as the working directory, and a profile
holds legacy junctions whose ``os.listdir`` raises. A sanitised fixture removes exactly the
condition that breaks production. :func:`test_real_home_launch` therefore runs the bootstrap
against the real profile, uncorrupted, and says out loud when the machine cannot exercise it.
"""
from __future__ import annotations

import json
import os
import re
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
skipped: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def skip(reason: str) -> None:
    """Record a case this environment cannot exercise, out loud.

    A branch that never fires reads exactly like a branch that was tested, so it gets its own
    counter and its own line rather than quietly becoming a pass.
    """
    skipped.append(reason)
    print(f"  SKIP {reason}")


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


def ask_the_server(home: Path | None, cwd: Path, method: str,
                   source: str | None = None) -> tuple[dict | None, str]:
    """Start the server through servers.mcp.json and send one JSON-RPC call to it.

    ``home`` of None means "do not touch HOME at all" - used by the real-home case, which is
    only worth anything if the environment is the one production runs in.
    """
    env = dict(os.environ)
    env.pop("PLUGIN_ROOT", None)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # os.path.expanduser reads USERPROFILE on Windows
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", source or bootstrap_source()],
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

        def send(payload: dict) -> None:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        # A real server answers tools/list only after the handshake, so do it whenever the
        # caller is asking for something other than initialize itself.
        if method != "initialize":
            send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05"}})
            proc.stdout.readline()
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 1, "method": method,
              "params": {"protocolVersion": "2024-11-05"}})
        line = proc.stdout.readline()
        result = json.loads(line) if line.strip() else None
    except (OSError, ValueError):
        # OSError, not just BrokenPipeError: a bootstrap that dies before writing anything
        # leaves a stdin that fails with EINVAL rather than EPIPE, and this suite has to be able
        # to point at that case instead of dying on it.
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
    check("**" in source and "recursive=True" in source,
          "bootstrap descends to the marketplace depth (glob '**', recursive)")


# The bootstrap as it stood before this was fixed: it walks directories by hand, unguarded, and
# it treats the working directory as a root. The host supplies that working directory, so this
# is the version that killed the plugin on every Windows machine. It is kept here as a negative
# control - see test_real_home_launch - and it must not be what ships.
UNGUARDED_BOOTSTRAP = (
    "import os,sys;_n=os.path.join('mcp','agent_server.py');"
    "_r=[p for p in (os.environ.get('PLUGIN_ROOT',''),os.getcwd()) if p]+"
    "[os.path.expanduser(p) for p in ('~/.minimax/plugins','~/.mavis/plugins',"
    "'~/.minimax/v2/plugin-cache','~/.minimax/v2/plugin-import')];"
    "_ls=lambda b:(os.listdir(b) if os.path.isdir(b) else []);"
    "_ok=lambda d:(lambda m:os.path.isfile(m) and 'kaggle-agent' in "
    "open(m,encoding='utf-8').read())(os.path.join(d,'.minimax-plugin','plugin.json'));"
    "_p=next((os.path.join(x,_n) for b in _r for x in [b]+[os.path.join(b,n) for n in _ls(b)]"
    "+[os.path.join(b,n,m) for n in _ls(b) for m in _ls(os.path.join(b,n))] if _ok(x)),None);"
    "_p is None and sys.exit('kaggle-agent: cannot locate '+_n+'; tried '+', '.join(_r));"
    "sys.path.insert(0,os.path.dirname(_p));"
    "exec(compile(open(_p,encoding='utf-8').read(),_p,'exec'),"
    "{'__name__':'__main__','__file__':_p,'__package__':None})"
)


# ------------------------------------------- the environment the host actually launches in
def unreadable_children(directory: Path) -> list[str]:
    """Children that claim to be directories and then refuse to be listed.

    Windows keeps a set of legacy junctions under the user profile - ``Application Data``,
    ``My Documents``, ``SendTo`` and friends. ``os.path.isdir`` is True for them while
    ``os.listdir`` raises ``WinError 5``, and that is what killed the bootstrap before this
    case existed. A plain ``mklink /J`` junction does NOT reproduce it (verified: its listdir
    succeeds), so the condition cannot be faked in a temp directory - it has to be the real
    profile or the case cannot run at all.
    """
    blocked: list[str] = []
    try:
        names = os.listdir(directory)
    except OSError:
        return blocked
    for name in names:
        child = directory / name
        try:
            if not os.path.isdir(child):
                continue
            os.listdir(child)
        except OSError:
            blocked.append(name)
    return blocked


def test_real_home_launch() -> None:
    print("servers.mcp.json bootstrap: launched the way the host launches it")
    home = Path(os.path.expanduser("~"))
    blocked = unreadable_children(home)
    if not blocked:
        skip(f"no unreadable child under {home} - this environment cannot exercise the failure")
        return
    print(f"       ({len(blocked)} unreadable children here, e.g. {blocked[0]!r})")

    if agent_server.locate() is None:
        skip("no installed kaggle-agent package here to launch")
        return

    reply, err = ask_the_server(None, home, "tools/list")
    tools = ((reply or {}).get("result") or {}).get("tools") or []
    names = sorted(tool.get("name", "") for tool in tools)
    check(len(tools) > 0,
          f"real HOME, cwd=$HOME -> the server answers tools/list ({len(tools)} tools)")
    check("kaggle_quota" in names,
          "and the tool this bug was reported through is among them")
    check("PermissionError" not in err and "Traceback" not in err,
          f"and stderr is clean ({err[:140]!r})")

    # Negative control, and the part that makes the three checks above mean anything. This case
    # claims the environment holds a directory the bootstrap cannot read. Prove that here, now,
    # by running the bootstrap that used to ship: it has to die here, or the profile does not
    # hold what we just found and a passing case above would be measuring nothing.
    reply_old, err_old = ask_the_server(None, home, "tools/list", source=UNGUARDED_BOOTSTRAP)
    check(not reply_old and ("PermissionError" in err_old or "Traceback" in err_old),
          f"and it can still tell: the unguarded bootstrap dies right here ({err_old.strip()[:130]!r})")


def test_bootstrap_is_platform_neutral() -> None:
    print("servers.mcp.json bootstrap: nothing in it belongs to one operating system")
    source = bootstrap_source()
    for name in ("ntpath", "winreg", "os.uname", "sys.platform", "os.getcwd"):
        check(name not in source, f"the bootstrap never mentions {name}")
    check("\\" not in source, "no backslash path literal")
    # A drive letter is a letter, a colon and then a separator. Plain "[A-Za-z]:" also matches
    # a one-letter lambda parameter, which is not a drive letter and would fail for no reason.
    check(not re.search(r"[A-Za-z]:[\\/]", source), "no drive letter")
    check("os.path.join" in source, "path building goes through os.path.join")
    check("os.path.expanduser" in source, "home expansion goes through os.path.expanduser")
    check("glob.glob" in source,
          "the walk is glob, which swallows OSError on every platform alike")


def test_missing_roots_exit_cleanly(tmp: Path) -> None:
    print("servers.mcp.json bootstrap: a machine where none of the roots exist")
    source = bootstrap_source()
    for i, root in enumerate(("~/.minimax/plugins", "~/.mavis/plugins",
                               "~/.minimax/v2/plugin-cache", "~/.minimax/v2/plugin-import")):
        source = source.replace(root, "~/.ka-absent-%d" % i)
    home = tmp / "home-boot-absent"
    home.mkdir(parents=True, exist_ok=True)
    reply, err = ask_the_server(home, home, "initialize", source=source)
    check(reply is None, f"nothing to serve, so nothing is served ({reply})")
    check("cannot locate" in err, f"and it says which roots it tried ({err.strip()[:120]!r})")
    check("Traceback" not in err,
          "a missing install exits with a message, not a traceback")


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
        test_bootstrap_is_platform_neutral()
        test_missing_roots_exit_cleanly(tmp)
        test_real_home_launch()
        test_this_machine()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"\nPROBE_MARKETPLACE_FAILED ({len(failures)})")
        for line in failures:
            print(f"  - {line}")
        return 1
    if skipped:
        print(f"\n{len(skipped)} case(s) this machine could not exercise:")
        for line in skipped:
            print(f"  - {line}")
    print("\nPROBE_MARKETPLACE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
