"""Multi-account Kaggle credential store for the kaggle-cli plugin.

One file holds every account, so a user can work across several Kaggle identities and
switch between them without re-authenticating. Nothing here points at one person's
machine: the store lives under the user's own home directory.

Storage: ``<home>/.kaggle-cli/accounts.json``::

    {
      "active": "work",
      "accounts": {
        "work":  {"token": "KGAT_...", "username": "alice", "added": "2026-09-27T..."},
        "hobby": {"token": "KGAT_...", "username": "bob",   "added": "2026-09-27T..."}
      }
    }

Resolution order for a token:

  1. ``KAGGLE_API_TOKEN`` in the environment - the host or a shell already set it. This
     wins so a one-off override never fights the saved selection.
  2. The active account in this store.
  3. ``KAGGLE_KEY`` as a last-resort pair for CI-style setups.

A legacy single-account ``credentials.json`` from an earlier version is migrated into
``work`` on first read, so upgrading never loses a token.

Naming
------
An account has two names, and keeping them separate is what makes the store readable:

- the **key** (stored ``name``) is the handle used by every tool call. It may be a
  username-derived default or an alias the user chose. It is never derived implicitly at
  read time, so a name a user typed keeps working.
- the **username** is the Kaggle identity, stored alongside the token.

User-facing output leads with the username when it is known, because that is what
identifies the account, and shows the key as the secondary label. So an account keyed
``qwyi123`` displays as ``qwyi123``, and an account the user aliased to ``work`` displays
as ``qwyi123 (work)`` - the identity first, their label second.

``add_account`` derives the key from the username when no name is given, so pasting a
token does not produce a meaningless label. ``rename_account`` changes the key in place,
keeping the token, the username and the added date, and refusing to overwrite an existing
account rather than merging two identities.

Only the secret and the username are stored. No token is ever written to the plugin
package, to a log, or to the output of a tool that a user reads.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

APP_DIR_NAME = ".kaggle-cli"
ACCOUNTS_FILE = "accounts.json"
LEGACY_FILE = "credentials.json"
DEFAULT_ACCOUNT = "work"
# A name is used as a filename-independent key and echoed in commands, so keep it tame.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class AccountError(ValueError):
    """Raised for an unusable account name or an unknown account."""


def default_name_for(username: str) -> str:
    """Derive a sensible account name from a Kaggle username.

    Kaggle usernames are already unique per account, so using one as the account name makes
    the stored list self-describing: seeing ``qwyi123`` in the list tells you exactly which
    identity it is, which a generic label like ``work`` does not. A user who prefers a
    meaningful alias can still pass an explicit name - this is only the fallback.

    Returns "" when there is no usable username, so the caller can fall back further.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", (username or "").strip())
    return cleaned[:64]


def store_path() -> Path:
    """Where this plugin keeps its accounts: ``<home>/.kaggle-cli/accounts.json``."""
    return Path(os.path.expanduser("~")) / APP_DIR_NAME / ACCOUNTS_FILE


def legacy_path() -> Path:
    return Path(os.path.expanduser("~")) / APP_DIR_NAME / LEGACY_FILE


def _empty() -> dict:
    return {"active": None, "accounts": {}}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:  # keep the file owner-only where the platform allows it
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def load() -> dict:
    """Read the account store, migrating a legacy single-token file on first use."""
    data = _read(store_path())
    if not isinstance(data.get("accounts"), dict):
        data = _empty()
    legacy = _read(legacy_path())
    if legacy.get("token") and DEFAULT_ACCOUNT not in data["accounts"]:
        # A previous version kept one token; keep the user logged in across the upgrade.
        #
        # But only if the migrated account is genuinely new. If the user has since renamed
        # the default account, DEFAULT_ACCOUNT is absent because it was renamed *away*, not
        # because it never existed - and re-migrating here would resurrect the old label and
        # silently duplicate the credential. Detect that by the username the legacy file
        # already carried, and skip when some account already claims that identity.
        legacy_username = (legacy.get("username") or "").strip()
        already_present = any(
            (acct.get("username") or "").strip() == legacy_username
            for name, acct in data["accounts"].items()
            if legacy_username
        )
        if not already_present:
            data["accounts"][DEFAULT_ACCOUNT] = {
                "token": legacy["token"],
                "username": legacy_username,
                "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if not data.get("active"):
                data["active"] = DEFAULT_ACCOUNT
            _write(data)
    active = data.get("active")
    if active not in data["accounts"]:
        data["active"] = next(iter(data["accounts"]), None)
    return data


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise AccountError(
            "account name must be 1-64 chars of letters, digits, dot, dash or underscore, "
            f"starting with a letter or digit; got {name!r}"
        )
    return name


def add_account(name: str, token: str, username: str = "") -> str:
    """Create or replace an account. Returns the name it was stored under.

    ``name`` may be empty, in which case the name is derived from ``username``. That is the
    common case: the user pastes a token, the CLI already knows who it belongs to, and
    inventing a generic label loses information the store already had.
    """
    token = (token or "").strip()
    if not token:
        raise AccountError("empty token; nothing was saved")
    username = (username or "").strip()
    name = (name or "").strip() or default_name_for(username)
    if not name:
        raise AccountError(
            "cannot derive an account name: pass name=<name> or username=<kaggle username>"
        )
    name = _check_name(name)
    data = load()
    data["accounts"][name] = {
        "token": token,
        "username": username,
        "added": data["accounts"].get(name, {}).get("added")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not data.get("active"):
        data["active"] = name
    _write(data)
    return name


def rename_account(old: str, new: str) -> str:
    """Rename an account in place, keeping its token, username and added date.

    Renaming is a key change, not a delete plus an add, so nothing about the credential is
    touched - which is the whole reason to prefer it over remove+add. The active pointer is
    carried across. Refuses to clobber an existing account rather than silently merging two
    identities, because a merge would pick one token arbitrarily and lose the other.
    """
    old = _check_name(old)
    new = _check_name(new)
    if old == new:
        return new
    data = load()
    if old not in data["accounts"]:
        raise AccountError(
            f"no such account: {old} (have: {', '.join(sorted(data['accounts'])) or 'none'})"
        )
    if new in data["accounts"]:
        raise AccountError(
            f"'{new}' already exists; renaming to it would discard one of the two accounts. "
            f"Remove it first, or pick a different name."
        )
    # Rebuild the dict so the renamed key lands in a stable position.
    rebuilt = {}
    for key, acct in data["accounts"].items():
        if key == old:
            acct = dict(acct)
            # Remember where this account came from, so the UI can mention it once and the
            # user can find a token they filed under the old label.
            prior = acct.get("renamed_from")
            acct["renamed_from"] = f"{prior} -> {old}" if prior else old
        rebuilt[new if key == old else key] = acct
    data["accounts"] = rebuilt
    if data.get("active") == old:
        data["active"] = new
    _write(data)
    return new


def remove_account(name: str) -> bool:
    """Delete an account. Returns True if it existed. Active falls back to another."""
    name = _check_name(name)
    data = load()
    if name not in data["accounts"]:
        return False
    del data["accounts"][name]
    if data.get("active") == name:
        data["active"] = next(iter(data["accounts"]), None)
    _write(data)
    return True


def use_account(name: str) -> str:
    """Make an account active and return its name."""
    name = _check_name(name)
    data = load()
    if name not in data["accounts"]:
        raise AccountError(f"no such account: {name} (have: {', '.join(data['accounts']) or 'none'})")
    data["active"] = name
    _write(data)
    return name


def active_name() -> Optional[str]:
    return load().get("active")


def active_token() -> Optional[str]:
    data = load()
    acct = data["accounts"].get(data.get("active") or "")
    return (acct or {}).get("token") or None


def list_accounts() -> list[dict]:
    """Every account with its label and whether it is active. No tokens.

    ``display`` is what a user-facing list should lead with: the Kaggle username when it is
    known, because that is the identity, with the stored name as the secondary label. Keeping
    the stored name in the key means a user-chosen alias still works everywhere, while the
    display name stays unambiguous when two aliases are easy to confuse.
    """
    data = load()
    active = data.get("active")
    out = []
    for name, acct in data["accounts"].items():
        username = acct.get("username", "")
        out.append(
            {
                "name": name,
                "username": username,
                "display": username or name,
                "active": name == active,
                "added": acct.get("added", ""),
                "renamed_from": acct.get("renamed_from", "") or None,
            }
        )
    return sorted(out, key=lambda a: (not a["active"], a["name"]))


def resolve_token() -> tuple[Optional[str], str]:
    """Return (token, source) for the first available credential."""
    env = (os.environ.get("KAGGLE_API_TOKEN") or "").strip()
    if env:
        return env, "KAGGLE_API_TOKEN environment variable"
    data = load()
    name = data.get("active")
    if name:
        token = (data["accounts"].get(name) or {}).get("token")
        if token:
            return token, f"account '{name}' ({store_path()})"
    legacy = _read(legacy_path())
    if legacy.get("token"):
        return legacy["token"], f"legacy store ({legacy_path()})"
    key = (os.environ.get("KAGGLE_KEY") or "").strip()
    if key:
        return key, "KAGGLE_KEY environment variable"
    return None, "not configured"


def status() -> dict:
    """A login summary safe to print: no secret, only where it came from."""
    token, source = resolve_token()
    data = load()
    return {
        "configured": bool(token),
        "source": source,
        "store_path": str(store_path()),
        "store_exists": store_path().exists(),
        "active": data.get("active"),
        "accounts": list_accounts(),
        "username_hint": (data["accounts"].get(data.get("active") or "") or {}).get("username", "")
        or (os.environ.get("KAGGLE_USERNAME") or "").strip(),
    }


def _cli(argv: list[str]) -> int:
    """Small CLI for maintenance and debugging. Never prints a token."""
    if not argv:
        argv = ["status"]
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "status":
            st = status()
            print(f"configured: {st['configured']}")
            print(f"active: {st['active'] or '(none)'}")
            print(f"source: {st['source']}")
            for a in st["accounts"]:
                ident = a["username"] or a["name"]
                if a["username"] and a["username"] != a["name"]:
                    ident += f"  (name: {a['name']})"
                print(f"  {'*' if a['active'] else ' '} {ident:<32} {a['name'] if a['username'] else ''}")
            print(f"store: {st['store_path']}")
            return 0 if st["configured"] else 1
        if cmd == "add":
            if len(rest) < 2:
                print("usage: credentials.py add <name> <token> [username]", file=sys.stderr)
                return 2
            print(f"saved '{add_account(rest[0], rest[1], rest[2] if len(rest) > 2 else '')}'")
            return 0
        if cmd == "use":
            print(f"active: {use_account(rest[0])}")
            return 0
        if cmd == "rename":
            if len(rest) < 2:
                print("usage: credentials.py rename <old> <new>", file=sys.stderr)
                return 2
            print(f"renamed to: {rename_account(rest[0], rest[1])}")
            return 0
        if cmd == "remove":
            print("removed" if remove_account(rest[0]) else "no such account")
            return 0
        if cmd == "print-token":
            # Consumed by a parent process to populate its own environment; never shown.
            print(resolve_token()[0] or "")
            return 0
    except AccountError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
