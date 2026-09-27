"""Entry point for the kaggle-cli plugin: manage accounts, and run the Kaggle CLI.

The plugin's ``bin/kaggle-cli.cmd`` (Windows) and ``bin/kaggle-cli.sh`` (macOS/Linux)
forward every invocation here. Doing the work in Python avoids the fragile part of batch
scripting and keeps one implementation shared with the MCP server.

Usage::

    kaggle-cli accounts                       list accounts, mark the active one
    kaggle-cli login [--as NAME] [TOKEN]      add or replace an account (prompts if no token)
    kaggle-cli use NAME                       switch the active account
    kaggle-cli remove NAME                    forget an account
    kaggle-cli whoami                         active account, token source, Kaggle's view
    kaggle-cli <kaggle args...>               run the Kaggle CLI, authenticated

The token is resolved from the environment, then the active saved account, then
KAGGLE_KEY. It is placed in this process's environment for the child CLI and never
printed by any command except the two that exist only to hand it to a parent process.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import credentials  # noqa: E402

TIMEOUT = 900  # a kernels push uploads a notebook; give the CLI room


def _kaggle(args: list[str]) -> int:
    token, source = credentials.resolve_token()
    if not token:
        print(
            "Kaggle is not signed in.\n"
            "Add an account:  kaggle-cli login --as <name> <ACCESS_TOKEN>\n"
            "Create a token at https://www.kaggle.com/settings/account",
            file=sys.stderr,
        )
        return 2
    env = {**os.environ, "KAGGLE_API_TOKEN": token}
    try:
        return subprocess.run(
            [sys.executable, "-m", "kaggle", *args], env=env, timeout=TIMEOUT
        ).returncode
    except subprocess.TimeoutExpired:
        print(f"kaggle {' '.join(args)} timed out after {TIMEOUT}s", file=sys.stderr)
        return 124


def _print_status() -> None:
    st = credentials.status()
    print(f"configured: {st['configured']}")
    print(f"source: {st['source']}")
    print(f"active: {st['active'] or '(none)'}")
    for a in st["accounts"]:
        mark = "*" if a["active"] else " "
        label = a["username"] or "(unknown user)"
        print(f"  {mark} {a['name']:<20} {label}")
    if not st["accounts"]:
        print("  (no saved accounts)")
    print(f"store: {st['store_path']}")


def _prompt_token() -> str:
    try:
        import getpass

        print("Paste your Kaggle access token (input hidden).")
        print("Create one at https://www.kaggle.com/settings/account")
        return getpass.getpass("Access token: ")
    except (ImportError, EOFError, KeyboardInterrupt):
        return ""


def main(argv: list[str]) -> int:
    if not argv:
        _print_status()
        return 0

    cmd, rest = argv[0].lower(), argv[1:]

    if cmd == "login":
        # login [--as NAME] [TOKEN] [USERNAME]
        name, positional = credentials.DEFAULT_ACCOUNT, []
        i = 0
        while i < len(rest):
            if rest[i] == "--as" and i + 1 < len(rest):
                name, i = rest[i + 1], i + 2
            else:
                positional.append(rest[i])
                i += 1
        token = positional[0] if positional else _prompt_token()
        if not token or not token.strip():
            print("no token entered; nothing was saved", file=sys.stderr)
            return 2
        username = positional[1] if len(positional) > 1 else ""
        try:
            saved = credentials.add_account(name, token, username)
        except credentials.AccountError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"saved account '{saved}'")
        credentials.use_account(saved)
        print(f"active: {saved}")
        return _kaggle(["config", "view"])

    if cmd == "use":
        if not rest:
            print("usage: kaggle-cli use <account>", file=sys.stderr)
            return 2
        try:
            name = credentials.use_account(rest[0])
        except credentials.AccountError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"active: {name}")
        return _kaggle(["config", "view"])

    if cmd in ("remove", "rm"):
        if not rest:
            print("usage: kaggle-cli remove <account>", file=sys.stderr)
            return 2
        try:
            existed = credentials.remove_account(rest[0])
        except credentials.AccountError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"removed {rest[0]}" if existed else f"no such account: {rest[0]}")
        return 0 if existed else 1

    if cmd in ("accounts", "ls", "list"):
        _print_status()
        return 0

    if cmd == "whoami":
        _print_status()
        print()
        return _kaggle(["config", "view"])

    if cmd == "status":
        _print_status()
        return 0 if credentials.status()["configured"] else 1

    return _kaggle(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
