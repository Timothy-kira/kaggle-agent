#!/usr/bin/env python
"""Optional GitHub transport for handoff sync, with no hard dependency on git.

Why this exists
---------------
A handoff document is only useful if the next agent can find it. The local file already
covers a same-machine relay (Claude Code and MiniMax Code share the filesystem); a remote
repo additionally covers a cross-machine relay. But ``git`` is frequently absent on a
clean machine - a fresh Windows or macOS box has no ``git`` until the user installs it -
and a plugin that assumes ``git`` exists is a plugin that fails for many users.

So the transport is chosen at runtime and degrades, in this order:

1. ``git``  - if ``git`` is on PATH and a remote is configured, push/pull normally.
2. ``api``  - if a GitHub token is available, use the REST Contents API via
   ``urllib`` (standard library only). This is the path most users will take, because
   it needs no ``git`` at all - only a token.
3. ``local`` - if neither is available, the sync is reported as unavailable and the
   caller keeps the document local. Nothing fails, nothing is lost.

Authentication
--------------
Two ways to get a token, because different users are comfortable with different flows:

* **Device flow (browser, no secret to paste).** The plugin POSTs to
  ``/login/device/code``, the user visits https://github.com/login/device, types the
  short code and approves in the browser, and the plugin polls
  ``/login/oauth/access_token``. This needs only a ``client_id`` - never a pasted secret -
  and shows the requested scopes on the approval page. It is the flow a general plugin
  should prefer, but it does require the user to have registered an app once to obtain a
  ``client_id``, and the client_id is the user's, never baked into the package.
* **Fine-grained PAT (env or store).** ``GITHUB_TOKEN`` / ``GH_TOKEN`` or the plugin's
  per-user store. Simpler for users who already have one, and it needs no app registration.

Token precedence: ``GITHUB_TOKEN`` -> ``GH_TOKEN`` -> plugin store. A token is never
written to the config file, never printed by a tool, and never committed; only its
source and the authenticated login are ever reported.

Nothing here reaches the network unless a tool explicitly asks it to. ``capabilities()``
is a pure local probe, so a user can ask "can this machine sync?" without any request
leaving the box.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

API_ROOT = "https://api.github.com"
OAUTH_HOST = "https://github.com"
USER_AGENT = "kaggle-agent-handoff"
DEVICE_CODE_TTL = 900  # GitHub device codes expire after 15 minutes


# --------------------------------------------------------------------------- config


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def config_path() -> str:
    return os.path.join(_home(), "config.json")


def load_config() -> dict[str, Any]:
    """Read the plugin's non-secret config (repo, branch, client_id). Missing file -> {}."""
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict[str, Any]) -> str:
    path = config_path()
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    return path


def _github_config() -> dict[str, Any]:
    return load_config().get("github", {}) or {}


# ----------------------------------------------------------------------- credentials


def _store_path() -> str:
    return os.path.join(_home(), "credentials.json")


def _load_store() -> dict[str, Any]:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_store(data: dict[str, Any]) -> None:
    path = _store_path()
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    try:
        # Best effort; Windows ignores POSIX modes and may not support this on all FS.
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _gh_cli_token() -> Optional[str]:
    """Read a token the GitHub CLI has already stored, without ever going through a paste.

    Many users authenticate with ``gh auth login`` in a browser and never touch a token. Their
    credential already exists at ``~/.config/gh/hosts.yml``; copying it out of there by hand
    would put it in a shell history or a chat transcript, which is exactly what this file exists
    to avoid. So read it in place, and only when the user has run gh's own login first.

    The file is read with a tiny YAML reader rather than a dependency: the hosts file is a shallow
    ``hosts: {host: {oauth_token: ...}}`` map, and a whole YAML engine for that would be a
    dependency this plugin otherwise does not need. Anything unexpected returns None, which
    falls through to the next source rather than guessing.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        path = os.path.join(base, "GitHub CLI", "hosts.yml")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
        path = os.path.join(base, "gh", "hosts.yml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    # The real file nests the token two levels deep:
    #     github.com:
    #         user: someone
    #         oauth_token: ghp_xxx
    # so a token is whatever follows an "oauth_token:" key once the key itself has been seen,
    # regardless of what indent level the key sits at. Resetting on a *less-indented* line
    # would work for one shape and silently fail for the other, so only a new oauth_token key
    # or a blank/comment line interrupts the value.
    want_value = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("oauth_token:"):
            want_value = True
            inline = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            if inline:
                return inline
            continue
        if want_value:
            # still inside the token's value: indented, and not a new key
            if raw[:1] in (" ", "\t") and ":" not in stripped:
                value = stripped.strip('"').strip("'")
                if value:
                    return value
            want_value = False
    return None


def resolve_token() -> tuple[Optional[str], str]:
    """Return ``(token, source)`` from env, the GitHub CLI, or the store, or ``(None, reason)``.

    Precedence: ``GITHUB_TOKEN`` -> ``GH_TOKEN`` -> GitHub CLI's own store -> plugin store.

    The CLI source is third on purpose. An explicit environment variable is the most local and the
    most deliberate, so it wins; a browser login through ``gh`` is a real credential the user
    created deliberately, so it is honoured without ever being copied into this process's memory
    by a human; and the plugin's own store remains the last resort for users who configured
    everything through this plugin.

    Never logs the value. The source string is safe to show the user.
    """
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(env_name)
        if tok and tok.strip():
            return tok.strip(), env_name
    cli_tok = _gh_cli_token()
    if cli_tok and cli_tok.strip():
        return cli_tok.strip(), "gh-cli (browser login)"
    store = _load_store()
    tok = store.get("github_token")
    if tok and str(tok).strip():
        return str(tok).strip(), "store"
    return None, "none"


def set_token(token: str) -> str:
    """Persist a token to the per-user store (called only after an explicit user action)."""
    store = _load_store()
    store["github_token"] = token.strip()
    _save_store(store)
    return _store_path()


def clear_token() -> bool:
    store = _load_store()
    if "github_token" in store:
        store.pop("github_token")
        _save_store(store)
        return True
    return False


def client_id() -> Optional[str]:
    for env_name in ("GITHUB_CLIENT_ID", "GH_CLIENT_ID"):
        val = os.environ.get(env_name)
        if val and val.strip():
            return val.strip()
    return _github_config().get("client_id")


def set_client_id(value: str) -> str:
    cfg = load_config()
    cfg.setdefault("github", {})["client_id"] = value.strip()
    return save_config(cfg)


# ------------------------------------------------------------------------ capabilities


def _git_version() -> Optional[str]:
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        )
        return (proc.stdout or "").strip() or "git"
    except (subprocess.SubprocessError, OSError):
        return "git"


def whoami() -> tuple[Optional[str], str]:
    """Authenticated login, or ``(None, reason)``. Requires a token; pure API call."""
    token, source = resolve_token()
    if not token:
        return None, f"no token ({source})"
    data, _err, code = api("GET", "/user", token=token)
    if code == 200 and isinstance(data, dict):
        return data.get("login"), f"token via {source}"
    return None, f"token via {source}, but /user did not return a profile"


def capabilities() -> dict[str, Any]:
    """Local, network-free report of what this machine can do.

    Used by the skill to decide whether to offer a GitHub sync or stay local, and to
    give the user a precise next step instead of a generic failure.
    """
    cfg = _github_config()
    token, token_source = resolve_token()
    git = _git_version()
    repo = cfg.get("repo")
    return {
        "git_installed": bool(git),
        "git": git,
        "token_available": bool(token),
        "token_source": token_source,
        "client_id_configured": bool(client_id()),
        "repo": repo,
        "branch": cfg.get("branch", "main"),
        # git is preferred when present *and* we can authenticate; otherwise api.
        "transport": "git" if git else ("api" if token else "local"),
        "sync_available": bool(git or token),
        "note": (
            "git installed; a remote with credentials is still required for a push"
            if git and not token
            else ("API transport available" if token else "no transport: handoff stays local")
        ),
    }


# ------------------------------------------------------------------------------- api


def api(
    method: str,
    path: str,
    token: Optional[str] = None,
    body: Optional[dict[str, Any]] = None,
    raw: bool = False,
) -> tuple[Any, str, int]:
    """Minimal GitHub REST call. Returns ``(data, error_text, status)``.

    ``raw=True`` sends the body verbatim (used by the OAuth token endpoints, which want
    form encoding and are served from the oauth host, not the API host).
    """
    token = token or resolve_token()[0]
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data: Optional[bytes] = None
    if body is not None:
        if raw:
            data = urllib.parse.urlencode(body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:  # 4xx/5xx carry a JSON body worth keeping
        payload = exc.read().decode("utf-8", "replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"network error reaching GitHub: {exc}", 0

    try:
        parsed = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        parsed = payload
    if isinstance(parsed, dict) and status >= 400 and "message" in parsed:
        return parsed, str(parsed.get("message")), status
    return parsed, "" if status < 400 else f"HTTP {status}", status


# ----------------------------------------------------------------------- device flow


def begin_device_flow(scope: str = "repo") -> dict[str, Any]:
    """Start a browser device flow. Returns the user code and the URL to visit.

    This never blocks; the user approves in their browser and a later ``finish_device_flow``
    call collects the result. Kept as two calls so the model can show the code and yield.
    """
    cid = client_id()
    if not cid:
        return {
            "ok": False,
            "error": (
                "no GitHub client_id configured. Register an OAuth App (Developer settings -> "
                "OAuth Apps -> New OAuth App), tick 'Enable Device Flow', and set the Client ID "
                "with github_client_id, or use a fine-grained PAT instead."
            ),
        }
    data, err, code = api(
        "POST",
        f"{OAUTH_HOST}/login/device/code",
        body={"client_id": cid, "scope": scope},
        raw=True,
    )
    if code != 200 or not isinstance(data, dict) or "device_code" not in data:
        return {"ok": False, "error": err or f"device flow request failed (HTTP {code})"}
    # Persist the device_code + interval so finish_device_flow can pick it up.
    store = _load_store()
    store["device_flow"] = {
        "device_code": data["device_code"],
        "interval": data.get("interval", 5),
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri"),
        "expires_in": data.get("expires_in", DEVICE_CODE_TTL),
        "started": int(time.time()),
        "client_id": cid,
    }
    _save_store(store)
    return {
        "ok": True,
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
        "expires_in": data.get("expires_in", DEVICE_CODE_TTL),
        "message": (
            f"Open {data.get('verification_uri', 'https://github.com/login/device')} in a browser, "
            f"enter the code {data.get('user_code')}, and approve. Then call "
            "github_auth_finish to store the token."
        ),
    }


def finish_device_flow(store_token: bool = True) -> dict[str, Any]:
    """Poll once for the device-flow result and (optionally) persist the token.

    The caller drives the polling cadence: a short ``time.sleep`` between calls, honouring
    the ``interval`` GitHub returned. Returns ``pending`` while the user has not approved.
    """
    store = _load_store()
    flow = store.get("device_flow")
    if not flow:
        return {"ok": False, "error": "no device flow in progress; call github_auth_begin first"}
    started = int(flow.get("started", 0))
    if time.time() - started > int(flow.get("expires_in", DEVICE_CODE_TTL)):
        store.pop("device_flow", None)
        _save_store(store)
        return {"ok": False, "error": "device code expired; start a new flow"}

    data, err, code = api(
        "POST",
        f"{OAUTH_HOST}/login/oauth/access_token",
        body={
            "client_id": flow.get("client_id"),
            "device_code": flow.get("device_code"),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        raw=True,
    )
    if isinstance(data, dict) and data.get("access_token"):
        store.pop("device_flow", None)
        _save_store(store)
        token = data["access_token"]
        if store_token:
            set_token(token)
        return {
            "ok": True,
            "stored": store_token,
            "scope": data.get("scope"),
            "login": whoami()[0],
        }
    # GitHub signals "not yet" with a specific error string; surface it verbatim.
    message = err or (data.get("error_description") if isinstance(data, dict) else "") or ""
    if "authorization_pending" in message:
        return {"ok": False, "pending": True, "message": message or "authorization pending"}
    if "slow_down" in message:
        return {"ok": False, "pending": True, "message": "slow_down: increase the poll interval"}
    if "access_denied" in message:
        store.pop("device_flow", None)
        _save_store(store)
        return {"ok": False, "error": "access denied in the browser"}
    return {"ok": False, "error": message or f"unexpected response (HTTP {code})"}


# ------------------------------------------------------------------------- repo access


def _repo() -> Optional[str]:
    return _github_config().get("repo")


def ensure_repo(private: bool = True, name: Optional[str] = None) -> dict[str, Any]:
    """Create the configured repo if it does not exist yet.

    External and hard to undo in bulk, so the caller must only invoke this after the user
    explicitly asked for a repo to be created. Creating an empty private repo is cheap and
    low-risk, but it is still an outward action and is never done silently.
    """
    repo = name or _repo()
    if not repo:
        return {"ok": False, "error": "no repo configured; set github.repo to owner/name first"}
    data, err, code = api("GET", f"/repos/{repo}")
    if code == 200:
        return {"ok": True, "created": False, "repo": repo, "detail": "repo already exists"}
    if code != 404:
        return {"ok": False, "error": err or f"HTTP {code} checking repo"}
    payload: dict[str, Any] = {"name": repo.split("/")[-1], "private": bool(private), "auto_init": True}
    data, err, code = api("POST", "/user/repos", body=payload)
    if code not in (200, 201):
        return {"ok": False, "error": err or f"HTTP {code} creating repo"}
    return {"ok": True, "created": True, "repo": data.get("full_name", repo), "private": bool(private)}


def _default_branch(repo: str, token: str) -> str:
    cfg_branch = _github_config().get("branch")
    if cfg_branch:
        return str(cfg_branch)
    data, _err, code = api("GET", f"/repos/{repo}", token=token)
    if code == 200 and isinstance(data, dict) and data.get("default_branch"):
        return str(data["default_branch"])
    return "main"


def push_file(repo: str, path: str, content: str, message: str, branch: Optional[str] = None) -> dict[str, Any]:
    """Create or update one text file via the Contents API (no git required).

    The Contents API is a per-file endpoint, which is exactly the shape a handoff sync is:
    a handful of small markdown/json files that a commit updates one at a time.
    """
    token, source = resolve_token()
    if not token:
        return {"ok": False, "error": f"no token ({source})"}
    branch = branch or _default_branch(repo, token)
    url_path = f"/repos/{repo}/contents/{urllib.parse.quote(path.lstrip('/'))}"

    # Existing file -> need its sha to update.
    existing, _err, code = api("GET", f"{url_path}?ref={urllib.parse.quote(branch)}", token=token)
    body: dict[str, Any] = {
        "message": message,
        "content": __import__("base64").b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if code == 200 and isinstance(existing, dict) and existing.get("sha"):
        body["sha"] = existing["sha"]
        data, err, code = api("PUT", url_path, token=token, body=body)
        action = "updated"
    else:
        data, err, code = api("PUT", url_path, token=token, body=body)
        action = "created"
    if code not in (200, 201):
        return {"ok": False, "error": err or f"HTTP {code}", "action": action}
    commit = (data or {}).get("commit", {}) if isinstance(data, dict) else {}
    return {
        "ok": True,
        "action": action,
        "repo": repo,
        "path": path,
        "branch": branch,
        "commit": (commit.get("sha") or "")[:7],
    }


def fetch_file(repo: str, path: str, branch: Optional[str] = None) -> dict[str, Any]:
    """Read one text file from a repo, or return an error if it is absent."""
    token, source = resolve_token()
    if not token:
        return {"ok": False, "error": f"no token ({source})"}
    branch = branch or _default_branch(repo, token)
    url_path = f"/repos/{repo}/contents/{urllib.parse.quote(path.lstrip('/'))}?ref={urllib.parse.quote(branch)}"
    data, err, code = api("GET", url_path, token=token)
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "error": err or f"HTTP {code}", "path": path}
    import base64

    content = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    return {"ok": True, "repo": repo, "path": path, "branch": branch, "content": content, "sha": data.get("sha")}


def list_dir(repo: str, path: str = "", branch: Optional[str] = None) -> dict[str, Any]:
    """List the entries of one directory in a repo (one level, non-recursive)."""
    token, source = resolve_token()
    if not token:
        return {"ok": False, "error": f"no token ({source})"}
    branch = branch or _default_branch(repo, token)
    clean = path.strip("/")
    url_path = f"/repos/{repo}/contents/{urllib.parse.quote(clean)}" if clean else f"/repos/{repo}/contents"
    url_path += f"?ref={urllib.parse.quote(branch)}"
    data, err, code = api("GET", url_path, token=token)
    if code != 200 or not isinstance(data, list):
        return {"ok": False, "error": err or f"HTTP {code}"}
    return {
        "ok": True,
        "repo": repo,
        "path": clean or "/",
        "branch": branch,
        "entries": [
            {"name": e.get("name"), "type": e.get("type"), "size": e.get("size")}
            for e in data
        ],
    }
