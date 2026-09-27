#!/usr/bin/env python
"""MCP server exposing the Kaggle CLI as tools (stdio JSON-RPC 2.0, standard library only).

Started by the host as ``python -B ./mcp/kaggle_server.py`` (see ``servers.mcp.json``), so the
package needs no Windows-specific launcher and runs the same on Windows, macOS and Linux.
Credentials are resolved by ``mcp/credentials.py`` from the KAGGLE_API_TOKEN env var, this
plugin's per-user store (``~/.kaggle-cli/credentials.json``), or KAGGLE_KEY - so no token is ever
shipped in the package or printed by a tool. If the interpreter the host started does not have
the Kaggle CLI, the server probes PATH for one that does.

Tools
  kaggle_auth_status       is Kaggle signed in, from where, as whom (never the secret)
  kaggle_accounts          list / add / switch / remove the saved accounts
  kaggle_config_view       the CLI's resolved config: username, auth method, path
  kaggle_quota             remaining GPU and TPU hours, and the refresh date
  kaggle_accelerators      which accelerators can be requested, and what quota allows
  kaggle_kernel_launch     push a run with an explicit time limit, accelerator and account
  kaggle_kernel_verify     did the run actually get the accelerator it asked for
  kaggle_kernel_retire     back up, delete, and confirm deletion of a quota-burning kernel
  kaggle_kernels_list      list notebooks; ``mine`` filters to the token's account
  kaggle_kernels_status    one notebook's run state (idle/running/complete/error)
  kaggle_kernels_push      upload a folder holding notebook.ipynb + kernel-metadata.json
  kaggle_kernels_output    download a finished notebook's output files
  kaggle_kernels_logs      download a notebook run's logs
  kaggle_competitions_list search competitions
  kaggle_competitions_forums   read a competition's discussion topics and messages
  kaggle_competitions_leaderboard  read a competition's leaderboard
  handoff_status           what handoffs exist, and whether this machine can sync one
  handoff_write            write the relay document, derived from the experiment tree
  handoff_read             read a handoff written by this or another agent
  handoff_sync             push a handoff to a repo, or report honestly why it cannot
  github_auth              GitHub auth for sync: status / device flow / logout / configure
  kaggle_log_monitor       log-watch config: get (every cycle) / set interval / targets
  kaggle_presence          is the user present or away: get / set / record / reset / status
  kaggle_experiment_tree   the RSI tree: read (before every node) / record / plan / status
  kaggle_search_engine     which engine a DISCOVERY search uses: ask / use / describe
  kaggle_sources           the evidence store: add / extract / link / review / coverage / doctor

Wire protocol: one JSON object per line on stdin, one per line on stdout.
Only ``initialize``, ``notifications/initialized``, ``tools/list`` and ``tools/call``
are answered; anything else gets a JSON-RPC error rather than a crash, so an
unexpected client message degrades into a clean error instead of a dead server.

Every tool runs the real CLI in a subprocess, so behaviour matches what a human gets
typing the same command. Output is truncated so one huge listing cannot blow up the
model's context.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import credentials  # noqa: E402  (local module, resolved next to this file)
import experiment_tree  # noqa: E402
import github_sync  # noqa: E402
import graphstate  # noqa: E402
import deps  # noqa: E402
import handoff  # noqa: E402
import logmonitor  # noqa: E402
import presence  # noqa: E402
import searchengine  # noqa: E402
import sources as srclib  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
MAX_OUTPUT = 20000
SUBPROCESS_TIMEOUT = 900  # kernels push downloads the notebook; give it room
MAX_RUN_SECONDS = 43200  # 12h, the platform ceiling for one notebook run


def _kaggle_command() -> list[str] | None:
    """How to invoke the Kaggle CLI from this process.

    Deliberately never does ``import kaggle`` in this process: importing that package runs
    its CLI entry point, which prints an authentication prompt to stdout and would corrupt
    the JSON-RPC stream this server speaks. The interpreter is probed in a throwaway
    subprocess instead.

    The host starts this server with whatever ``python`` is on PATH, which usually already
    has the CLI. If it does not, fall back to any interpreter on PATH that can import kaggle,
    so the same package works on a machine set up differently.
    """
    candidates = [sys.executable]
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe and exe not in candidates:
            candidates.append(exe)
    for exe in candidates:
        try:
            probe = subprocess.run(
                [exe, "-c", "import kaggle, sys; sys.exit(0)"],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return [exe, "-m", "kaggle"]
    return None


# --------------------------------------------------------------------- accelerators

def _normalize_accelerator(spec: str) -> tuple[str, dict]:
    """Map a requested accelerator spec onto the fields the platform actually reads.

    ``kernels push --accelerator`` is accepted by the CLI but is **not** persisted onto
    the kernel: a kernel pushed with ``--accelerator tpu`` still comes back with
    ``enableTpu: false``. The only fields the platform honours live in
    ``kernel-metadata.json``, so the accelerator has to be written there. This returns the
    metadata fields to set alongside the CLI flag.
    """
    s = (spec or "").strip().lower()
    if not s or s in ("none", "cpu"):
        return "none", {"enable_gpu": False, "enable_tpu": False}
    if s.startswith("tpu"):
        return "tpu", {"enable_gpu": False, "enable_tpu": True}
    if s.startswith("gpu"):
        return "gpu", {"enable_gpu": True, "enable_tpu": False}
    # Unknown spec: hand it to the CLI and let the platform decide, but do not guess
    # metadata fields, because writing the wrong one silently changes the run.
    return s, {}


def _read_metadata(folder: str) -> Optional[dict]:
    path = os.path.join(folder, "kernel-metadata.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_metadata(folder: str, meta: dict) -> None:
    path = os.path.join(folder, "kernel-metadata.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def _set_accelerator_in_metadata(folder: str, spec: str) -> tuple[bool, str]:
    """Ensure kernel-metadata.json requests the accelerator. Returns (changed, note)."""
    meta = _read_metadata(folder)
    if meta is None:
        return False, "no readable kernel-metadata.json; the accelerator flag alone will not stick"
    kind, fields = _normalize_accelerator(spec)
    if not fields:
        return False, f"accelerator '{spec}' has no known metadata mapping; passed through to the CLI only"
    changed = any(meta.get(k) != v for k, v in fields.items())
    if changed:
        meta.update(fields)
        _write_metadata(folder, meta)
    want = ", ".join(f"{k}={v}" for k, v in fields.items())
    return changed, f"kernel-metadata.json set to {want}"


def _effective_accelerator(meta: dict) -> str:
    """What the platform will actually give this kernel, per its metadata."""
    if meta.get("enable_gpu"):
        return "gpu"
    if meta.get("enable_tpu"):
        return "tpu"
    shape = (meta.get("machine_shape") or meta.get("machineShape") or "").strip()
    if shape:
        return f"machine:{shape}"
    return "none"


def _live_accelerator(ref: str) -> Optional[str]:
    """Read a pushed kernel's real accelerator from the Kaggle API.

    The CLI's ``kernels status`` line does not carry the accelerator, so this asks the
    same v1 API the CLI uses. The ``search`` query parameter turned out to be a fuzzy
    match that ignores the term, so this lists the owner's notebooks and matches the ref
    exactly instead. Returns 'gpu' / 'tpu' / 'none' / 'machine:<shape>', or None when
    the kernel cannot be read. This is the ground truth for verifying a launch.
    """
    import urllib.request

    token, _src = credentials.resolve_token()
    if not token or "/" not in ref:
        return None
    owner, slug = ref.split("/", 1)
    try:
        url = (
            "https://www.kaggle.com/api/v1/kernels/list"
            f"?user={urllib.parse.quote(owner)}&pageSize=100&sortBy=dateCreated"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        if not isinstance(row, dict) or row.get("ref") != ref:
            continue
        if row.get("enableGpu"):
            return "gpu"
        if row.get("enableTpu"):
            return "tpu"
        shape = (row.get("machineShape") or "").strip()
        return f"machine:{shape}" if shape else "none"
    return None

TOOLS: list[dict[str, Any]] = [
    {
        "name": "kaggle_quota",
        "description": (
            "Show this account's remaining accelerator quota: GPU and TPU hours used, remaining "
            "and total, and when the quota refreshes. Check it before starting a long run so a "
            "12h job is not cut off mid-way."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "kaggle_accelerators",
        "description": (
            "Accelerator options and market state for this account: which types can be requested, "
            "what the quota currently allows, and the accelerator names accepted by push. Use it "
            "to decide between GPU and TPU for a run before pushing."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "kaggle_kernel_launch",
        "description": (
            "Create or update a notebook run with an explicit time limit and accelerator, on a "
            "chosen account's quota. The accelerator is written into kernel-metadata.json as well "
            "as passed to the CLI, because the CLI flag alone is not honoured. "
            "timeout_seconds is capped at 43200 (12h), the platform maximum. "
            "Re-pushing the same folder resumes the same notebook rather than starting a new one. "
            "Returns the ref to poll with kaggle_kernels_status and to read with kaggle_kernels_logs. "
            "REFUSES to launch unless the run was declared first: pass declares=<node id> from "
            "kaggle_experiment_tree action=\"declare\", so the result has somewhere to land instead "
            "of existing only in the conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder with notebook.ipynb and kernel-metadata.json."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Run time limit in seconds. Values above 43200 are clamped to 12h.",
                },
                "accelerator": {
                    "type": "string",
                    "description": "gpu, tpu, or none. Written into kernel-metadata.json so it takes effect.",
                },
                "account": {
                    "type": "string",
                    "description": "Which saved account's quota to charge. Defaults to the active account.",
                },
                "competition": {
                    "type": "string",
                    "description": (
                        "Competition slug the run belongs to. Defaults to 'id' in "
                        "kernel-metadata.json. Required: without it the result cannot be tied to a node."
                    ),
                },
                "declares": {
                    "type": "string",
                    "description": (
                        "Node id of an unsettled declaration from kaggle_experiment_tree "
                        "action=\"declare\". Required."
                    ),
                },
            },
            "required": ["folder", "declares"],
        },
    },
    {
        "name": "kaggle_kernel_verify",
        "description": (
            "Check that a notebook is actually using the accelerator it was meant to use, by reading "
            "its live kernel record (enableGpu/enableTpu/machineShape) and comparing it to what was "
            "requested. Use it right after a launch: a kernel silently running on CPU burns no "
            "accelerator quota but also does not do the work, and one that mismatches must be "
            "retired with kaggle_kernel_retire before re-launching, or it keeps consuming quota."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "username/slug of the notebook."},
                "expected": {
                    "type": "string",
                    "description": "The accelerator it should be using: gpu, tpu, or none.",
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "kaggle_kernel_retire",
        "description": (
            "Safely take a notebook off the accelerator: back up its source and output to a local "
            "folder, delete it, then confirm the deletion actually took effect. Confirmation matters "
            "because a kernel that fails to delete keeps consuming quota. Pass dry_run=true to see "
            "what would be deleted first. The backup is written before the delete, never after."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "username/slug of the notebook to retire."},
                "backup_dir": {
                    "type": "string",
                    "description": "Local folder for the backup. Source, output and a manifest land here.",
                },
                "dry_run": {"type": "boolean", "default": False, "description": "Report only, delete nothing."},
                "skip_backup": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip the backup. Only for a notebook that is worthless.",
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "kaggle_kernel_pull",
        "description": (
            "Download someone else's public notebook, optionally generating kernel-metadata.json "
            "so it can be modified and pushed as your own. This is the first step of adapting a "
            "strong public solution instead of writing one from scratch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kernel": {"type": "string", "description": "owner/slug of the public notebook."},
                "path": {"type": "string", "description": "Local destination folder."},
                "metadata": {"type": "boolean", "default": True, "description": "Generate kernel-metadata.json."},
            },
            "required": ["kernel"],
        },
    },
    {
        "name": "kaggle_accounts",
        "description": (
            "Manage saved Kaggle accounts: list them (the active one is marked), add or replace "
            "one, rename one, switch the active account, or remove one. An account has a stored "
            "name used by every call and a Kaggle username; when adding without a name it is "
            "derived from the username, and list output leads with the username because that is "
            "the identity. To sign in, call this with action='add' and the token the user "
            "supplies - that is the normal path and it needs no terminal. If the user prefers "
            "not to paste a token into the conversation, 'kaggle-cli login --as <name> <token>' "
            "writes to the same store and is offered as an alternative."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "use", "remove", "rename", "status"],
                    "default": "list",
                    "description": "list saved accounts; add/replace one; switch; rename; remove.",
                },
                "name": {"type": "string", "description": "Account name (letters, digits, . - _). Omit on add to derive it from username."},
                "token": {"type": "string", "description": "Access token, only for action='add'."},
                "username": {"type": "string", "description": "Optional Kaggle username label; also the source of the default account name."},
                "new_name": {"type": "string", "description": "The new name, for action='rename'."},
            },
            "required": [],
        },
    },
    {
        "name": "kaggle_auth_status",
        "description": (
            "Report whether Kaggle is signed in: which account is active, where its credential "
            "came from, and the account list. Never returns a secret. Call this first when any "
            "Kaggle call fails with 401/403. If not configured, tell the user to run "
            "'kaggle-cli login --as <name> <token>' in a terminal."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "kaggle_competitions_forums",
        "description": (
            "Read a competition's discussion forum: topic list, or the messages of one topic. "
            "This is the structured way to collect community discussion - it returns authors, "
            "timestamps, votes and comment counts, which scraping the page would miss."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "competition": {"type": "string", "description": "Competition slug."},
                "topic_id": {"type": "integer", "description": "Omit to list topics; set to read messages."},
                "page": {"type": "integer", "description": "1-based page for topic listing."},
                "search": {"type": "string", "description": "Filter topics by substring."},
            },
            "required": ["competition"],
        },
    },
    {
        "name": "kaggle_competitions_leaderboard",
        "description": (
            "Read a competition's leaderboard. Use it to rank teams before deciding whose "
            "notebooks are worth reading."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "competition": {"type": "string", "description": "Competition slug."},
                "page": {"type": "integer", "description": "1-based page."},
                "show": {"type": "boolean", "default": True, "description": "Print to stdout."},
            },
            "required": ["competition"],
        },
    },
    {
        "name": "kaggle_config_view",
        "description": "Show the resolved Kaggle configuration: username, auth method, config path.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "kaggle_kernels_list",
        "description": (
            "List Kaggle notebooks. Use mine=true to list only the notebooks owned by the "
            "authenticated account. Optionally filter by a search string."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Filter notebooks by substring."},
                "mine": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only notebooks owned by the authenticated account.",
                },
                "page": {"type": "integer", "description": "1-based page number."},
            },
            "required": [],
        },
    },
    {
        "name": "kaggle_kernels_status",
        "description": (
            "Get the current state of a notebook: idle, running, complete or error, plus the "
            "last run time. Ref is 'username/slug'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "username/slug"}},
            "required": ["ref"],
        },
    },
    {
        "name": "kaggle_kernels_push",
        "description": (
            "Upload a folder containing notebook.ipynb and kernel-metadata.json to Kaggle, "
            "creating the notebook or updating an existing one. This starts a run. "
            "Use --tags to label it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Local folder to upload."},
                "tags": {"type": "string", "description": "Optional comma-separated tags."},
                "privacy": {"type": "string", "enum": ["public", "private"], "description": "Default private."},
            },
            "required": ["folder"],
        },
    },
    {
        "name": "kaggle_kernels_output",
        "description": "Download the output files of a finished notebook run into a local folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "username/slug"},
                "path": {"type": "string", "description": "Local destination folder."},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "kaggle_kernels_logs",
        "description": "Download the logs of a notebook run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "username/slug"},
                "path": {"type": "string", "description": "Local destination folder."},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "kaggle_competitions_list",
        "description": "Search Kaggle competitions by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"search": {"type": "string", "description": "Competition name substring."}},
            "required": ["search"],
        },
    },
    {
        "name": "handoff_status",
        "description": (
            "Report the handoff state for a competition: whether a document exists, where it "
            "lives, the current base node and experiment count, and whether this machine can "
            "sync it to a remote repo. Makes no network call. Call it to decide whether to "
            "offer a handoff and whether a remote sync is possible at all."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "competition": {
                    "type": "string",
                    "description": "Competition or project name. Omit to report every handoff on disk.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "handoff_write",
        "description": (
            "Write or refresh the relay document for a competition: the rules and link, the "
            "current base and its cost, everything already refuted, and the next experiment. "
            "Reads tree.json for the base and the refuted list, so the document cannot claim a "
            "base the tree does not have. Never automatic - call it when the user asks for a "
            "handoff or at a checkpoint they agreed to."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "competition": {"type": "string", "description": "Competition or project name."},
                "title": {"type": "string", "description": "Human-readable competition title."},
                "url": {"type": "string", "description": "Link to the competition."},
                "task": {"type": "string", "description": "One or two lines on what the task is."},
                "metric": {"type": "string", "description": "The scoring metric, if not set on the base node."},
                "deadline": {"type": "string", "description": "Competition deadline as stated."},
                "next": {"type": "string", "description": "The next experiment to run."},
                "hypothesis": {"type": "string", "description": "What that experiment should improve, and why."},
                "workspace": {"type": "string", "description": "Where the code lives."},
                "account": {"type": "string", "description": "Kaggle account in use."},
                "kernels": {"type": "string", "description": "Notebook refs this work uses."},
                "quota_at_write": {"type": "string", "description": "Quota as of writing, so the next agent knows the budget."},
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gotchas the next agent must not rediscover the hard way.",
                },
                "tree": {
                    "type": "object",
                    "description": (
                        "Optional RSI experiment tree ({base, nodes}). Merged into the tree on disk "
                        "and is the source of truth for the base node, its metric and the refuted "
                        "list, so pass it whenever the experiment state changed."
                    ),
                },
            },
            "required": ["competition"],
        },
    },
    {
        "name": "handoff_read",
        "description": (
            "Read back a handoff document, for resuming work in a new session or handing it to "
            "another agent. Use this before starting experiments in an unfamiliar competition."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"competition": {"type": "string", "description": "Competition or project name."}},
            "required": ["competition"],
        },
    },
    {
        "name": "handoff_sync",
        "description": (
            "Push the handoff document and its experiment tree to a GitHub repo so another "
            "machine or agent can pick the work up. Picks its transport at runtime: git if it is "
            "installed and authenticated, otherwise the REST API with the stored token, otherwise "
            "reports that the handoff is local only. Creating a repo is an outward action, so it "
            "only happens when create_repo is true, which requires the user to have asked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "competition": {"type": "string", "description": "Competition or project name."},
                "repo": {"type": "string", "description": "owner/name. Defaults to the configured repo."},
                "path": {"type": "string", "description": "Folder inside the repo. Defaults to handoffs/<slug>."},
                "branch": {"type": "string", "description": "Branch. Defaults to the repo's default branch."},
                "message": {"type": "string", "description": "Commit message."},
                "create_repo": {
                    "type": "boolean",
                    "description": "Create the repo if missing. Only with the user's explicit request.",
                },
                "private": {"type": "boolean", "description": "Create it private. Defaults to true."},
            },
            "required": ["competition"],
        },
    },
    {
        "name": "github_auth",
        "description": (
            "Manage GitHub authentication for handoff sync. action=status reports the transport "
            "this machine can use and the configured repo without any network call. action=begin "
            "starts a browser device flow and returns a short code to enter at github.com/login/"
            "device, so no token is ever pasted. action=finish collects the result. action=logout "
            "removes the stored token. action=configure sets the repo, branch or OAuth client_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status, begin, finish, logout, or configure.",
                },
                "repo": {"type": "string", "description": "owner/name, for configure."},
                "branch": {"type": "string", "description": "Branch, for configure."},
                "client_id": {"type": "string", "description": "OAuth App client_id, for configure."},
                "scope": {"type": "string", "description": "Device-flow scope. Defaults to repo."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "kaggle_log_monitor",
        "description": (
            "Configure how experiment logs are watched. action='get' is what a monitoring "
            "subagent calls at the TOP OF EVERY POLL CYCLE to learn the current fetch interval - "
            "it re-reads from disk each time, so a change made while it runs takes effect on the "
            "next cycle with no restart. action='set' changes the interval and bumps a revision "
            "number, so you can confirm a change landed. action='target' registers a Kaggle kernel "
            "ref or a local log file to watch; action='clear' stops watching."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "status", "target", "clear", "reset"],
                    "default": "get",
                    "description": (
                        "get = read current config (call every cycle); set = change the interval; "
                        "status = human-readable summary; target/clear/reset = manage watches."
                    ),
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": (
                        f"New fetch interval in seconds, for action='set'. "
                        f"Clamped to {15}-{logmonitor.MAX_INTERVAL_SECONDS} and snapped to a "
                        f"{logmonitor.STEP_SECONDS}s step."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["kaggle", "local"],
                    "description": "What kind of log to watch, for action='target'.",
                },
                "ref": {"type": "string", "description": "Kaggle kernel ref owner/slug, for a kaggle target."},
                "path": {"type": "string", "description": "Local log file path, for a local target."},
            },
            "required": [],
        },
    },
    {
        "name": "kaggle_presence",
        "description": (
            "Is the user at the keyboard, or has the work been handed over? Read this at the "
            "TOP of any decision point that you might otherwise stop to ask about - it re-reads "
            "from disk each time, so a subagent that is already running picks up a mode change on "
            "its next decision. action='get' is the cheap read. 'present' (the default) means ask "
            "early and often, because a wrong assumption can waste a 12h run. 'away' means "
            "proceed unattended on conservative, reversible, recorded defaults instead of asking "
            "questions nobody is there to answer. 'away' lowers the ask threshold for decisions "
            "ONLY: it never authorises anything irreversible or externally visible - creating a "
            "repo, pushing a handoff, retiring a kernel, or spending the last of someone's quota "
            "still needs explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "record", "status", "reset"],
                    "default": "get",
                    "description": (
                        "get = read the mode (call at every decision point); set = the user said "
                        "they are here or leaving; record = log one auto-decision taken while "
                        "away and learn whether the budget still has room; status = human-readable "
                        "summary; reset = refill the away auto-advance budget."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": list(presence.VALID_MODES),
                    "description": "The new mode, for action='set'.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional context stored with the change, e.g. 'left for dinner'.",
                },
                "decision": {
                    "type": "string",
                    "description": (
                        "For action='record': the call you made on your own, e.g. "
                        "'ran the CPU profile notebook instead of asking to add a feature'."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "For action='record': why that was the safe default, so the user can audit "
                        "it on their return instead of re-deriving it."
                    ),
                },
                "budget": {
                    "type": "integer",
                    "description": f"For action='reset': a new budget (default {presence.DEFAULT_AUTO_ADVANCE_BUDGET}).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "kaggle_experiment_tree",
        "description": (
            "The RSI experiment tree: a validated DAG of ablation nodes that stops the next "
            "iteration from silently re-running what was already refuted. The shape is ENFORCED "
            "here, not suggested in prose: a node is rejected without a hypothesis, without a "
            "metric carrying the parent's number, without a real reason, without an operator and "
            "method family, or if its 'change' contains an 'and' (that is two experiments). Two "
            "node kinds: 'experiment' changes one thing and measures it; 'research' changes "
            "nothing and goes BACK to a source (forum / code / web / paper / model / dataset / "
            "rules / leaderboard) because a result made the current picture insufficient. "
            "action='read' is MANDATORY before recording: it returns readRevision, and 'record' "
            "refuses a stale or missing one. action='select' does NON-GREEDY parent selection "
            "over quality + progress + novelty with visit cooling, so the search is not "
            "starved by score-greedy expansion (arXiv 2607.28568 sec 5.2); action='board' shows "
            "method families, failures and which operators actually produced the gain. "
            "action='declare' announces an experiment BEFORE it is run and action='settle' records "
            "its result as a node parented by the declaration; kaggle_kernel_launch refuses any run "
            "that was not declared, so no result can live only in the conversation. "
            "How to think - what to try, when to branch, when to stop - is deliberately left to "
            "the agent; only the record's shape and the order of records are constrained."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "consider", "declare", "settle", "prune", "record", "plan",
                             "status", "select", "board", "replay", "compare", "policy",
                             "round_close", "anchor", "undo", "analyze", "review"],
                    "default": "read",
                    "description": (
                        "read = the tree plus the readRevision that authorises one write "
                        "(call this before EVERY node); consider = ask, at any step, whether what "
                        "you are about to do is already refuted, already known, or already in "
                        "flight, and whether it earns a node at all; declare = announce an "
                        "experiment before running it (change, hypothesis, parent, operator, "
                        "family, reason - no metric yet); settle = record the result of a "
                        "declaration, as a new node parented by it; prune = delete one USELESS "
                        "research node, and only that (an experiment is evidence and is never "
                        "deleted); record = add one node; plan = the kept / "
                        "refuted / in-flight / research summary to plan from; status = counts and "
                        "whether the tree is currently sound; select = non-greedy three-factor parent "
                        "selection returning a BATCH (quality + progress + novelty, visit "
                        "cooling, plus per-criterion regression and effective cost); board = the "
                        "experience board: families, failures by layer, and per-criterion EFC. "
                        "replay = 'dream' a policy over the recorded history at zero cost; "
                        "compare = score several policies and pick the best, never worse than the "
                        "deployed one; policy = create/use/deploy/list exploration policies; "
                        "round_close = archive the current round into the replay pool; anchor = "
                        "declare/query the held-out evaluation set; undo = step back the last "
                        "state change, including restoring a pruned node."
                    ),
                },
                "change": {
                    "type": "string",
                    "description": "For action='consider': the one thing you are about to do.",
                },
                "hypothesis": {
                    "type": "string",
                    "description": "For action='consider': why you expect it to matter.",
                },
                "node": {
                    "type": "string",
                    "description": "For action='prune': the id of the research node to delete.",
                },
                "declared": {
                    "type": "string",
                    "description": (
                        "For action='settle': the node id of the declaration this result settles. "
                        "A declaration can only be settled once."
                    ),
                },
                "policy": {
                    "type": "object",
                    "description": (
                        "For replay/compare: a policy or list of policies, each {id, label, params}. "
                        "params takes weights{score,progress,novelty,temperature}, betaCost, "
                        "betaParallel, workers, maxRounds. Policies are DATA; nothing is executed."
                    ),
                },
                "held_out": {
                    "type": "string",
                    "description": "For action='anchor': the evaluation set held back from evolution.",
                },
                "rule": {
                    "type": "string",
                    "description": "For action='anchor': the rule the anchor enforces, in your words.",
                },
                "competition": {
                    "type": "string",
                    "description": "Competition or project name; identifies which tree.",
                },
                "read_revision": {
                    "type": "integer",
                    "description": (
                        "The readRevision from action='read'. Required by action='record', and "
                        "refused if the tree changed since that read - read again and re-plan."
                    ),
                },
                "node": {
                    "type": "object",
                    "description": (
                        "The node to record. kind='experiment' needs: parent, change (one line, "
                        "no 'and'), hypothesis, metric{name,parent,result,delta}, verdict, reason, "
                        "artifacts. kind='research' needs: parent, question, targets (a list of "
                        "forum/code/web/paper/model/dataset/rules/leaderboard), verdict, reason, "
                        "opens (what this makes possible for a later experiment)."
                    ),
                },
                "new_base": {
                    "type": "string",
                    "description": "Node id to promote to base when this node is kept, if it should advance.",
                },
                "weights": {
                    "type": "object",
                    "description": (
                        "For action='select': optional weights for score / progress / novelty / "
                        "temperature, plus workers to size the returned batch. Defaults follow "
                        "arXiv 2607.28568 sec. 5.2."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": "For action='policy' create: the policy parameters to register.",
                },
                "policy_action": {
                    "type": "string",
                    "enum": ["create", "deploy", "list"],
                    "default": "list",
                    "description": "For action='policy': what to do with the policy registry.",
                },
                "policy_id": {
                    "type": "string",
                    "description": "For action='policy' deploy: the policy id to deploy.",
                },
                "label": {"type": "string", "description": "A human label for a policy."},
                "note": {"type": "string", "description": "Free-text note attached to the record."},
                "rounds": {
                    "type": "integer",
                    "description": "For replay/compare: replay over only the last N archived rounds.",
                },
            },
            "required": ["competition"],
        },
    },
    {
        "name": "kaggle_sources",
        "description": (
            "The evidence store: every paper, repository, dataset and forum thread the research "
            "touched, stored once and linked to the tree nodes that relied on it. action='add' "
            "stores a source and returns the existing one if the URL or arXiv id was already "
            "stored, rather than creating a rival record; action='extract' attaches the exact "
            "sentence that carries a claim, which is what makes a link checkable rather than "
            "merely asserted; action='link' ties a source to a node with a relation - supports, "
            "motivates, contradicts, supersedes - because those are different claims and a tree "
            "that flattens them cannot say which evidence justified a decision. action='backlink' "
            "answers the direction a ledger usually misses: what else does this paper support. "
            "action='review' reassembles the reasoning with its evidence attached. action='doctor' "
            "reports the optional plotting environment and installs nothing without consent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "extract", "link", "unlink", "get", "list", "search",
                             "backlink", "coverage", "review", "stats", "doctor", "install"],
                    "default": "list",
                    "description": (
                        "add = store a source; extract = attach the supporting quote; link / "
                        "unlink = bind to a node; get = read one; list / search = find; "
                        "backlink = which nodes cite this; coverage = how much of a tree is "
                        "backed; review = the full recap; stats = what the store holds; "
                        "doctor = report the optional environment; install = ONLY after the user "
                        "has agreed to it."
                    ),
                },
                "kind": {"type": "string", "enum": list(srclib.SOURCE_KINDS),
                         "description": "What kind of source this is."},
                "title": {"type": "string", "description": "Title of the source."},
                "url": {"type": "string", "description": "Canonical URL; the dedupe key."},
                "authors": {"type": "string", "description": "Authors."},
                "published": {"type": "string", "description": "Publication date."},
                "venue": {"type": "string", "description": "Journal, conference or repository."},
                "summary": {"type": "string",
                            "description": "What this source actually claims, in your own words."},
                "licence": {"type": "string",
                            "description": ("Its licence. Record it honestly: a missing licence is a "
                                            "finding, because it decides whether the work can be built on.")},
                "doi": {"type": "string", "description": "DOI."},
                "arxiv": {"type": "string", "description": "arXiv id, e.g. 2607.28568."},
                "notes": {"type": "string", "description": "Anything worth remembering."},
                "source_id": {"type": "string", "description": "Id of an existing source."},
                "quote": {"type": "string",
                          "description": ("The exact sentence that carries the claim. A link without "
                                          "one is a claim nobody can check.")},
                "claim": {"type": "string", "description": "Which claim this quote supports."},
                "locator": {"type": "string", "description": "Section or page."},
                "competition": {"type": "string", "description": "Which tree, for link / coverage / review."},
                "node_id": {"type": "string", "description": "Which node, for link / unlink."},
                "relation": {
                    "type": "string",
                    "enum": ["supports", "motivates", "contradicts", "supersedes"],
                    "default": "supports",
                    "description": "How this source relates to the node's claim.",
                },
                "query": {"type": "string", "description": "Free text, for search."},
                "needs_extract": {"type": "boolean",
                                  "description": "For search: only sources nobody has quoted yet."},
                "packages": {
                    "type": "array", "items": {"type": "string"},
                    "description": ("For action='install': optional packages. ONLY call this after the "
                                    "user has agreed - plotting works without any of them."),
                },
            },
            "required": [],
        },
    },
    {
        "name": "kaggle_search_engine",
        "description": (
            "Choose the search engine for a DISCOVERY search - the kind where you do not yet "
            "know the URL and have to find it. This is a real choice, so call action='ask' "
            "BEFORE running a discovery search: it returns the available engines and whether the "
            "user is present. If the user is present, ask them with ask_user and then apply their "
            "answer with action='use'. If the user is away, take the default, record it with "
            "kaggle_presence action='record', and continue without asking. Direct navigation is "
            "EXEMPT: opening a URL you already have (github.com/..., arxiv.org/..., "
            "huggingface.co/...) is not a search and never asks. Both engines were measured "
            "through the real browser: Bing renders fast and follows the host region unless "
            "setlang is pinned; Google redirects to a regional host and renders slowly, so wait "
            "before judging the screen - a blank first frame is not an empty result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["ask", "use", "describe"],
                    "default": "ask",
                    "description": (
                        "ask = called BEFORE a discovery search; returns the options plus the "
                        "presence mode so you know whether to ask the user or take the default. "
                        "use = apply the chosen engine. describe = current engine and why."
                    ),
                },
                "engine": {
                    "type": "string",
                    "enum": list(searchengine.ENGINES),
                    "description": "The engine to use, for action='use'.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional: also return the ready-made search URL for this query.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional language pin, e.g. 'en'. Stops Bing inheriting the host locale.",
                },
            },
            "required": [],
        },
    },
]

def _replay_worlds(doc: dict[str, Any], rounds: Any = None) -> list[dict[str, Any]]:
    """Which archived rounds to replay over. All of them, or the last N."""
    pool = doc.get("rounds") or []
    if isinstance(rounds, int) and rounds > 0:
        return pool[-rounds:]
    return pool


SERVER_INFO = {"name": "kaggle-agent", "version": "1.14.0"}


def run_kaggle(args: list[str]) -> tuple[int, str, str]:
    """Run the Kaggle CLI. Returns (returncode, stdout, stderr)."""
    token, source = credentials.resolve_token()
    if not token:
        return 2, "", (
            "Kaggle is not signed in, so no call can authenticate. Sign in from inside the "
            "conversation with kaggle_accounts action='add' name=<name> token=<ACCESS_TOKEN>, "
            "using a token the user supplies. If they prefer not to paste it here, "
            "'kaggle-cli login --as <name> <token>' writes to the same store. Then re-check with "
            "kaggle_auth_status."
        )
    cmd = _kaggle_command()
    if cmd is None:
        return 127, "", (
            "The Kaggle CLI is not installed for this interpreter. Install it with: pip install kaggle"
        )
    env = {**os.environ, "KAGGLE_API_TOKEN": token}
    try:
        proc = subprocess.run(
            [*cmd, *args],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"kaggle {' '.join(args)} timed out after {SUBPROCESS_TIMEOUT}s"
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        return 127, "", f"cannot run the Kaggle CLI: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def text_response(cmd: str, code: int, out: str, err: str) -> dict[str, Any]:
    """Build an MCP tool result, preferring stdout and appending stderr on failure."""
    if code != 0:
        body = (err or out).strip() or f"exit code {code}"
        if out.strip():
            body = f"{body}\n--- stdout ---\n{out.strip()}"
        text = f"$ {cmd}\nFAILED (exit {code})\n{body}"
    else:
        text = f"$ {cmd}\n{out.strip() or '(no output)'}"
    if len(text) > MAX_OUTPUT:
        half = MAX_OUTPUT // 2
        text = f"{text[:half]}\n... [{len(text) - MAX_OUTPUT} chars cut] ...\n{text[-half:]}"
    return {"content": [{"type": "text", "text": text}], "isError": code != 0}


def _accounts_text() -> str:
    """Account summary for a human or a model: identity first, stored name second, no token."""
    st = credentials.status()
    lines = [
        f"configured: {st['configured']}",
        f"active: {st['active'] or '(none)'}",
        f"source: {st['source']}",
    ]
    for a in st["accounts"]:
        mark = "*" if a["active"] else " "
        # The Kaggle username is the identity, so it leads. The stored name follows only when
        # it differs, so a user-chosen alias stays visible without looking like the account.
        if a.get("username") and a["username"] != a["name"]:
            ident = f"{a['username']}  (name: {a['name']})"
        else:
            ident = a["username"] or a["name"]
        lines.append(f"  {mark} {ident}")
        if a.get("renamed_from"):
            lines.append(f"      renamed from: {a['renamed_from']}")
    if not st["accounts"]:
        lines.append("  (no saved accounts)")
    return "\n".join(lines)


def tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Map a tool name plus arguments to a Kaggle CLI invocation."""
    if name == "kaggle_quota":
        return text_response("kaggle quota", *run_kaggle(["quota"]))

    if name == "kaggle_accelerators":
        # The CLI has no dedicated accelerator listing, so pair the live quota with the
        # documented `--accelerator` values. Quota is what actually constrains a run; the
        # names below are the ones the platform documents for kernel pushes.
        code, out, err = run_kaggle(["quota"])
        body = out.strip() or err.strip() or "(no quota reported)"
        body += (
            "\n\naccelerators accepted by kernels push --accelerator:"
            "\n  gpu / tpu            request a type; the platform picks the exact device"
            "\n  gpus=4               4 GPUs, when the account tier allows it"
            "\n  tpu=1 vm            a TPU VM (v3/v4e), for TPU-only workloads"
            "\n\nrule of thumb: a single long run should request one accelerator and"
            "\ntimeout_seconds <= 43200 (12h). Request more GPUs only when the job"
            "\nparallelises across devices, and check the quota above first."
        )
        return text_response("kaggle quota + accelerator options", 0 if code == 0 else code, body, "")

    if name == "kaggle_kernel_launch":
        folder = str(args["folder"])
        if not os.path.isdir(folder):
            return text_response(
                f"kaggle kernels push -p {folder}", 2, "",
                f"folder does not exist: {folder}. It needs notebook.ipynb and kernel-metadata.json.",
            )

        # The guarantee. A run may only happen against a declared experiment, so no result can
        # exist only in a chat transcript. This is the one place in the plugin that refuses to do
        # the user's work, and it refuses loudly rather than proceeding with a warning 閳?a soft
        # gate is a gate nobody has to walk through.
        _comp = str(args.get("competition") or "").strip() or str(
            (_read_metadata(folder) or {}).get("id") or "").strip()
        _declares = str(args.get("declares") or "").strip()
        if not _comp:
            return text_response(
                f"kaggle kernels push -p {folder}", 3, "",
                "a launch must name the competition, so its result has somewhere to land. Pass "
                'competition="<slug>", or put "id" in kernel-metadata.json.',
            )
        _pending = experiment_tree.pending_declarations(_comp)
        if not _declares:
            _avail = ", ".join(p["id"] for p in _pending) or "(none declared yet)"
            return text_response(
                f"kaggle kernels push -p {folder}", 3, "",
                f"no experiment was declared for {_comp!r}, so this run's result would exist "
                f"only in the conversation.\n\ndeclare it first, then launch with its id:\n"
                f"  1. kaggle_experiment_tree action=\"read\" competition=\"{_comp}\"\n"
                f"  2. kaggle_experiment_tree action=\"declare\" competition=\"{_comp}\" "
                f"read_revision=<that revision> node={{\"id\":\"e1\",\"change\":\"...\","
                f"\"hypothesis\":\"...\",\"parent\":null,\"operator\":\"draft\","
                f"\"family\":\"...\",\"reason\":\"...\"}}\n"
                f"  3. kaggle_kernel_launch ... declares=\"<node id>\"\n\n"
                f"declarations already open: {_avail}",
            )
        _nodes = (experiment_tree._current(experiment_tree.load(_comp)).get("nodes") or {})
        _open = {p["id"] for p in _pending}
        if _declares not in _open:
            _why = ("it does not exist" if _declares not in _nodes
                    else "it is not an unsettled declaration")
            return text_response(
                f"kaggle kernels push -p {folder}", 3, "",
                f"declares={_declares!r} was refused: {_why}.\nopen declarations: "
                + (", ".join(sorted(_open)) or "(none)"),
            )
        notes: list[str] = []
        # Which account pays. Done before the push so the run lands on the right quota.
        if args.get("account"):
            try:
                notes.append(f"account: {credentials.use_account(str(args['account']))}")
            except credentials.AccountError as exc:
                return text_response(f"kaggle kernels push -p {folder}", 2, "", f"error: {exc}")
        # The accelerator must be in the metadata: the CLI flag alone is silently ignored.
        if args.get("accelerator"):
            _, note = _set_accelerator_in_metadata(folder, str(args["accelerator"]))
            notes.append("accelerator: " + note)
        cmd = ["kernels", "push", "-p", folder]
        requested = args.get("timeout_seconds")
        if requested is not None:
            # 12h is the platform ceiling; ask for more and the run is cut off anyway.
            secs = min(int(requested), MAX_RUN_SECONDS)
            cmd += ["--timeout", str(secs)]
            if int(requested) > MAX_RUN_SECONDS:
                return text_response(
                    " ".join(cmd), 2, "",
                    f"requested {int(requested)}s exceeds the 12h platform maximum; "
                    f"clamped to {MAX_RUN_SECONDS}s (12h). Re-run with the clamped value to proceed.",
                )
        if args.get("accelerator"):
            cmd += ["--accelerator", str(args["accelerator"])]
        result = text_response(" ".join(cmd), *run_kaggle(cmd))
        if notes:
            result["content"][0]["text"] += "\nnotes: " + "; ".join(notes)
        return result

    if name == "kaggle_kernel_verify":
        ref = str(args["ref"])
        actual = _live_accelerator(ref) or "unknown"
        expected = (str(args.get("expected")) if args.get("expected") else "").strip().lower()
        body = f"ref: {ref}\nactual accelerator: {actual}"
        verdict = "unknown"
        if expected and actual != "unknown":
            verdict = "match" if actual == expected else "MISMATCH"
            body += f"\nexpected: {expected}\nverdict: {verdict}"
        if verdict == "MISMATCH":
            body += (
                f"\n\nThis notebook is NOT on the {expected} it was launched for. Retire it with "
                f"kaggle_kernel_retire (source and output are backed up first) and re-launch, or it "
                f"keeps consuming quota without doing the work."
            )
        elif verdict == "match":
            body += "\n\nAccelerator is as requested. No action needed."
        elif actual == "unknown":
            body += "\n\nCould not read the kernel record. Check it is pushed and the account is active."
        return text_response(f"kaggle kernels verify {ref}", 0, body, "")

    if name == "kaggle_kernel_retire":
        ref = str(args["ref"])
        backup = str(args.get("backup_dir") or os.path.join(os.getcwd(), "retired", ref.replace("/", "_")))
        sc0, so0, _ = run_kaggle(["kernels", "status", ref])
        info = f"ref: {ref}\nbackup: {backup}\nstatus before: {so0.strip() or '(unknown)'}"
        if args.get("dry_run"):
            info += "\n\nDRY RUN - nothing deleted. Re-run with dry_run=false to back up and delete."
            return text_response(f"retire {ref} (dry run)", 0, info, "")
        if args.get("skip_backup"):
            info += "\nbackup: SKIPPED"
        else:
            # Back up before deleting: a retired kernel that cannot be recovered is a lost run.
            os.makedirs(backup, exist_ok=True)
            pc, _po, pe = run_kaggle(["kernels", "pull", ref, "-p", backup, "-m"])
            info += f"\nbackup source: {'ok' if pc == 0 else 'FAILED ' + (pe or '')[:140]}"
            oc, _oo, oe = run_kaggle(["kernels", "output", ref, "-p", os.path.join(backup, "output")])
            info += f"\nbackup output: {'ok' if oc == 0 else ('no output yet' if '404' in (oe or '') else 'FAILED ' + (oe or '')[:140])}"
            try:
                with open(os.path.join(backup, "RETIRED.json"), "w", encoding="utf-8") as f:
                    json.dump({"ref": ref, "retired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "status_before": so0.strip()}, f, indent=1)
            except OSError:
                pass
        dc, do, de = run_kaggle(["kernels", "delete", ref, "-y"])
        if dc != 0:
            info += f"\n\nDELETE FAILED: {(de or do or '').strip()[:200]}\nThe kernel may still exist and still burn quota - do not assume it is gone."
            return text_response(f"retire {ref}", dc, info, "")
        info += "\n\ndelete accepted. Confirming it took effect..."
        gone, detail = False, ""
        for _ in range(4):
            time.sleep(2)
            stc, sto, ste = run_kaggle(["kernels", "status", ref])
            low = ((ste or "") + (sto or "")).lower()
            if stc != 0 and ("denied" in low or "not found" in low):
                gone, detail = True, low.strip()[:120]
                break
            lsc, lso, _ = run_kaggle(["kernels", "list", "--csv", "--search", ref.split("/")[-1]])
            if ref not in (lso or ""):
                gone, detail = True, "absent from the notebook list"
                break
        if gone:
            info += f"\nCONFIRMED deleted ({detail}). It will no longer consume quota."
            return text_response(f"retire {ref}", 0, info, "")
        info += (
            "\n\nNOT CONFIRMED: still present after delete. It may still be consuming quota - retry, "
            "or escalate. A kernel that will not delete is exactly what silently burns a budget."
        )
        return text_response(f"retire {ref}", 1, info, "")

    if name == "kaggle_kernel_pull":
        kernel = str(args["kernel"])
        cmd = ["kernels", "pull", kernel]
        if args.get("path"):
            dest = str(args["path"])
            os.makedirs(dest, exist_ok=True)
            cmd += ["-p", dest]
        if args.get("metadata", True):
            cmd.append("-m")
        code, out, err = run_kaggle(cmd)
        if code == 0 and args.get("path"):
            files = []
            for root, _dirs, names in os.walk(str(args["path"])):
                for f in sorted(names)[:100]:
                    files.append(os.path.relpath(os.path.join(root, f), str(args["path"])))
            out = (out or "") + "\ndownloaded:\n  " + "\n  ".join(files)
            out += (
                "\n\nBefore pushing, edit kernel-metadata.json: set \"id\" to your own "
                "owner/slug, and change the title. Pushing unchanged would write to the "
                "original author's notebook."
            )
        return text_response(f"kaggle kernels pull {kernel}", code, out, err)

    if name == "kaggle_accounts":
        action = str(args.get("action") or "list")
        try:
            if action == "add":
                if not args.get("token"):
                    return {
                        "content": [{"type": "text", "text": (
                            "Adding an account needs an access token (action='add' with token, "
                            "and optionally name and username). If the user has not supplied one, "
                            "ask them for it - that is the supported path and it works without "
                            "leaving the conversation. Give a name only if the user asked for "
                            "one; otherwise it is derived from the Kaggle username. The terminal "
                            "alternative 'kaggle-cli login --as <name> <token>' reaches the same "
                            "store, so offer it as a second option rather than a requirement."
                        )}],
                        "isError": True,
                    }
                # name is optional: credentials.add_account derives it from username when empty.
                saved = credentials.add_account(
                    str(args.get("name") or ""), str(args["token"]), str(args.get("username") or "")
                )
                credentials.use_account(saved)
                return {"content": [{"type": "text", "text": f"saved and activated '{saved}'\n\n{_accounts_text()}"}],
                        "isError": False}
            if action == "use":
                if not args.get("name"):
                    return {"content": [{"type": "text", "text": "name is required for action='use'"}],
                            "isError": True}
                return {"content": [{"type": "text", "text": f"active: {credentials.use_account(str(args['name']))}\n\n{_accounts_text()}"}],
                        "isError": False}
            if action == "rename":
                old = str(args.get("name") or "")
                new = str(args.get("new_name") or "")
                if not old or not new:
                    return {"content": [{"type": "text", "text": "action='rename' needs name=<old> and new_name=<new>"}],
                            "isError": True}
                renamed = credentials.rename_account(old, new)
                return {"content": [{"type": "text", "text": (
                    f"renamed '{old}' to '{renamed}'; token, username and added date are unchanged\n\n"
                    + _accounts_text())}],
                        "isError": False}
            if action == "remove":
                if not args.get("name"):
                    return {"content": [{"type": "text", "text": "name is required for action='remove'"}],
                            "isError": True}
                removed = credentials.remove_account(str(args["name"]))
                return {"content": [{"type": "text", "text": (
                    (f"removed {args['name']}\n\n" if removed else f"no such account: {args['name']}\n\n")
                    + _accounts_text())}],
                    "isError": not removed}
        except credentials.AccountError as exc:
            return {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True}
        if action == "status":
            return {"content": [{"type": "text", "text": _accounts_text()}], "isError": False}
        return {"content": [{"type": "text", "text": _accounts_text()}], "isError": False}

    if name == "kaggle_auth_status":
        head = _accounts_text()
        if not credentials.status()["configured"]:
            head += (
                "\n\nNot signed in. Sign in from inside the conversation with "
                "kaggle_accounts action='add' name=<name> token=<ACCESS_TOKEN>. The terminal "
                "alternative 'kaggle-cli login --as <name> <token>' uses the same store."
            )
        else:
            code, out, err = run_kaggle(["config", "view"])
            if code == 0 and out.strip():
                head += "\n\n" + out.strip()
        return {"content": [{"type": "text", "text": head}], "isError": False}

    if name == "kaggle_competitions_forums":
        comp = str(args["competition"])
        if args.get("topic_id"):
            # positional: competition then topic_id
            cmd = ["competitions", "topic-messages", comp, str(int(args["topic_id"])), "--csv"]
        else:
            cmd = ["competitions", "topics", "list", "-c", comp, "--csv"]
            if args.get("search"):
                cmd += ["--search", str(args["search"])]
            if args.get("page"):
                cmd += ["--page", str(int(args["page"]))]
        return text_response(f"kaggle {' '.join(cmd[1:3])}", *run_kaggle(cmd))

    if name == "kaggle_competitions_leaderboard":
        comp = str(args["competition"])
        cmd = ["competitions", "leaderboard", comp, "--show"]
        if args.get("page"):
            cmd += ["--page", str(int(args["page"]))]
        return text_response(f"kaggle competitions leaderboard {comp}", *run_kaggle(cmd))

    if name == "kaggle_kernels_list":
        cmd = ["kernels", "list", "--csv"]
        if args.get("mine"):
            cmd.append("--mine")
        if args.get("search"):
            cmd += ["--search", str(args["search"])]
        if args.get("page"):
            cmd += ["--page", str(int(args["page"]))]
        return text_response(" ".join(cmd), *run_kaggle(cmd))

    if name == "kaggle_kernels_status":
        ref = str(args["ref"])
        cmd = ["kernels", "status", ref]
        code, out, err = run_kaggle(cmd)
        # `kernels status` exits non-zero while a run is in flight on some CLI versions;
        # the status line itself is the answer, so do not report that as a failure.
        return text_response(f"kaggle kernels status {ref}", 0, out or err, "")

    if name == "kaggle_kernels_push":
        folder = str(args["folder"])
        if not os.path.isdir(folder):
            return text_response(
                f"kaggle kernels push -p {folder}",
                2,
                "",
                f"folder does not exist: {folder}. A push folder needs notebook.ipynb and "
                "kernel-metadata.json side by side.",
            )
        cmd = ["kernels", "push", "-p", folder]
        if args.get("tags"):
            cmd += ["--tags", str(args["tags"])]
        if args.get("privacy"):
            cmd += ["--privacy", str(args["privacy"])]
        return text_response(" ".join(cmd), *run_kaggle(cmd))

    if name in ("kaggle_kernels_output", "kaggle_kernels_logs"):
        sub = "output" if name.endswith("output") else "logs"
        ref = str(args["ref"])
        cmd = ["kernels", sub, ref]
        if args.get("path"):
            dest = str(args["path"])
            os.makedirs(dest, exist_ok=True)
            cmd += ["-p", dest]
        code, out, err = run_kaggle(cmd)
        if code == 0 and args.get("path"):
            listing = []
            for root, _dirs, files in os.walk(str(args["path"])):
                for f in files[:200]:
                    listing.append(os.path.relpath(os.path.join(root, f), str(args["path"])))
            out = (out or "") + ("\ndownloaded:\n  " + "\n  ".join(sorted(listing)[:200]) if listing else "")
        return text_response(f"kaggle kernels {sub} {ref}", code, out, err)

    if name == "kaggle_competitions_list":
        cmd = ["competitions", "list", "--csv", "--search", str(args["search"])]
        return text_response(" ".join(cmd), *run_kaggle(cmd))

    if name == "kaggle_config_view":
        return text_response("kaggle config view", *run_kaggle(["config", "view"]))

    if name == "handoff_status":
        comp = args.get("competition")
        if not comp:
            entries = handoff.list_all()
            if not entries:
                return text_response(
                    "handoff list", 0,
                    f"no handoff documents on disk.\nroot: {handoff.handoff_root()}\n\n"
                    "call handoff_write to create one, or handoff_status with a competition name",
                    "",
                )
            body = f"handoff root: {handoff.handoff_root()}\n\n"
            for e in entries:
                body += (
                    f"{e['slug']}\n"
                    f"  title      {e['title']}\n"
                    f"  base       {e.get('base') or '(none)'}\n"
                    f"  nodes      {e['nodes']}\n"
                    f"  written    {e.get('last_written') or '(unknown)'}\n"
                    f"  path       {e['path']}\n\n"
                )
            return text_response("handoff list", 0, body.rstrip(), "")

        caps = github_sync.capabilities()
        if not handoff.exists(str(comp)):
            return text_response(
                f"handoff status {comp}", 0,
                f"no handoff for '{comp}' yet.\n"
                f"would be written to: {handoff.handoff_path(str(comp))}\n"
                f"\nsync capability on this machine:\n"
                f"  transport   {caps['transport']}\n"
                f"  git         {'installed' if caps['git_installed'] else 'not installed'}\n"
                f"  token       {caps['token_source'] if caps['token_available'] else 'not configured'}\n"
                f"  repo        {caps.get('repo') or '(not configured)'}\n"
                f"  remote sync {'available' if caps['sync_available'] else 'unavailable - handoff will stay local'}",
                "",
            )
        tree = handoff.load_tree(str(comp))
        base = handoff.base_node(tree)
        chain = handoff.kept_chain(tree)
        bad = handoff.refuted(tree)
        body = (
            f"handoff for '{comp}'\n"
            f"  file        {handoff.handoff_path(str(comp))}\n"
            f"  tree        {handoff.tree_path(str(comp))}\n"
            f"  base node   {base.get('id') or '(none)'}\n"
            f"  base change {base.get('change') or '(none)'}\n"
            f"  base metric {(base.get('metric') or {}).get('name', '?')} = "
            f"{(base.get('metric') or {}).get('result', '?')}\n"
            f"  nodes       {len(tree.get('nodes') or {})} "
            f"({len(chain)} kept, {len(bad)} refuted)\n"
            f"\nsync capability:\n"
            f"  transport   {caps['transport']}\n"
            f"  git         {'installed' if caps['git_installed'] else 'not installed'}\n"
            f"  token       {caps['token_source'] if caps['token_available'] else 'not configured'}\n"
            f"  repo        {caps.get('repo') or '(not configured)'}\n"
        )
        return text_response(f"handoff status {comp}", 0, body, "")

    if name == "handoff_write":
        comp = str(args["competition"])
        # Only forward keys the caller actually set, so a partial update does not
        # blank out fields an earlier write recorded.
        meta = {k: args.get(k) for k in (
            "title", "url", "task", "metric", "deadline", "next", "hypothesis",
            "workspace", "account", "kernels", "quota_at_write", "constraints",
        ) if args.get(k) not in (None, "")}
        # The tree is what makes the document true: it supplies the base, the metric and
        # the refuted list, so it must be forwarded or the doc silently renders empty.
        tree = args.get("tree") if isinstance(args.get("tree"), dict) else None
        result = handoff.write(comp, meta, tree=tree)
        return text_response(
            f"handoff write {comp}", 0,
            f"wrote handoff for '{comp}'\n"
            f"  file   {result['handoff']}\n"
            f"  tree   {result['tree']}\n"
            f"  base   {result.get('base') or '(none)'}\n"
            f"  nodes  {result['nodes']}\n"
            f"  bytes  {result['bytes']}\n\n"
            f"read it back with handoff_read competition={comp}",
            "",
        )

    if name == "handoff_read":
        comp = str(args["competition"])
        doc = handoff.read(comp)
        if doc is None:
            return text_response(
                f"handoff read {comp}", 2, "",
                f"no handoff for '{comp}' at {handoff.handoff_path(comp)}",
            )
        return text_response(f"handoff read {comp}", 0, doc, "")

    if name == "handoff_sync":
        comp = str(args["competition"])
        if not handoff.exists(comp):
            return text_response(
                f"handoff sync {comp}", 2, "",
                f"no local handoff for '{comp}'; call handoff_write first",
            )
        caps = github_sync.capabilities()
        if not caps["sync_available"]:
            return text_response(
                f"handoff sync {comp}", 2, "",
                "no remote transport available on this machine: "
                f"git {'is' if caps['git_installed'] else 'is not'} installed and no GitHub token is "
                "configured. The handoff remains local at "
                f"{handoff.handoff_path(comp)} - nothing was lost, and a same-machine agent can "
                "still read it. To enable remote sync, run github_auth action=begin (browser "
                "device flow) or set GITHUB_TOKEN.",
            )
        repo = str(args.get("repo") or caps.get("repo") or "")
        if not repo:
            return text_response(
                f"handoff sync {comp}", 2, "",
                "no repo configured. Pass repo=owner/name, or run github_auth action=configure.",
            )
        if args.get("create_repo"):
            # Outward action: only ever reached because the caller asked for it.
            created = github_sync.ensure_repo(
                private=bool(args.get("private", True)), name=repo
            )
            if not created.get("ok"):
                return text_response(f"create repo {repo}", 2, "", str(created.get("error")))
        folder = str(args.get("path") or f"handoffs/{handoff.slugify(comp)}")
        branch = args.get("branch")
        message = str(args.get("message") or f"handoff: update {comp}")

        pushed: list[dict[str, Any]] = []
        problems: list[str] = []
        for filename in ("HANDOFF.md", "tree.json", "state.json"):
            local = os.path.join(handoff.comp_dir(comp), filename)
            if not os.path.isfile(local):
                continue
            with open(local, "r", encoding="utf-8") as fh:
                content = fh.read()
            res = github_sync.push_file(repo, f"{folder}/{filename}", content, message, branch)
            if res.get("ok"):
                pushed.append({"file": filename, "action": res.get("action"), "commit": res.get("commit")})
            else:
                problems.append(f"{filename}: {res.get('error')}")

        handoff.set_sync_state(comp, last_sync_repo=repo, last_sync_path=folder)
        if problems:
            body = (
                f"sync to {repo} partially failed\n"
                + "\n".join(f"  {p}" for p in problems)
                + f"\n\npushed {len(pushed)} of {len(pushed) + len(problems)} files"
            )
            return text_response(f"handoff sync {comp}", 1, body, "")
        body = (
            f"synced '{comp}' to {repo} at {folder}\n"
            + "\n".join(f"  {p['action']:<8} {p['file']:<14} {p['commit']}" for p in pushed)
        )
        return text_response(f"handoff sync {comp}", 0, body, "")

    if name == "github_auth":
        action = str(args.get("action") or "status")

        if action == "status":
            caps = github_sync.capabilities()
            cfg = github_sync.load_config().get("github", {})
            body = (
                f"git installed      {caps['git_installed']}  {caps.get('git') or ''}\n"
                f"token available    {caps['token_available']}  (source: {caps['token_source']})\n"
                f"client_id set      {caps['client_id_configured']}\n"
                f"repo               {caps.get('repo') or '(not configured)'}\n"
                f"branch             {caps.get('branch') or '(repo default)'}\n"
                f"selected transport {caps['transport']}\n"
                f"remote sync        {'available' if caps['sync_available'] else 'unavailable - handoff stays local'}\n"
                f"config file        {github_sync.config_path()}\n"
            )
            return text_response("github_auth status", 0, body, "")

        if action == "begin":
            res = github_sync.begin_device_flow(scope=str(args.get("scope") or "repo"))
            if not res.get("ok"):
                return text_response("github_auth begin", 2, "", str(res.get("error")))
            return text_response(
                "github_auth begin", 0,
                f"user code:   {res['user_code']}\n"
                f"open:        {res['verification_uri']}\n"
                f"expires in:  {res.get('expires_in')}s\n\n"
                f"{res['message']}",
                "",
            )

        if action == "finish":
            res = github_sync.finish_device_flow()
            if res.get("pending"):
                return text_response(
                    "github_auth finish", 0,
                    "still waiting for approval in the browser; call again after a few seconds",
                    "",
                )
            if not res.get("ok"):
                return text_response("github_auth finish", 2, "", str(res.get("error")))
            return text_response(
                "github_auth finish", 0,
                f"authenticated as {res.get('login')} (scope: {res.get('scope')})\n"
                f"token stored in {github_sync._store_path()}\n"
                f"now run github_auth action=configure to set the repo",
                "",
            )

        if action == "logout":
            removed = github_sync.clear_token()
            return text_response(
                "github_auth logout", 0,
                "token removed from the store" if removed else "no stored token to remove",
                "",
            )

        if action == "configure":
            cfg = github_sync.load_config()
            gh = cfg.setdefault("github", {})
            changed: list[str] = []
            for field in ("repo", "branch", "client_id"):
                if args.get(field):
                    gh[field] = str(args[field])
                    changed.append(field)
            if not changed:
                return text_response(
                    "github_auth configure", 2, "",
                    "nothing to set: pass repo, branch or client_id",
                )
            # Only the client_id lives here, and it is a public identifier. A token is never
            # written to config - github_sync keeps those in the separate credentials store.
            path = github_sync.save_config(cfg)
            return text_response(
                "github_auth configure", 0,
                f"updated: {', '.join(changed)}\nwritten to {path}",
                "",
            )

        return text_response("github_auth", 2, "", f"unknown action: {action}")

    if name == "kaggle_log_monitor":
        action = str(args.get("action") or "get")
        try:
            if action == "get":
                # The hot path: a monitoring subagent calls this once per cycle. It must
                # reflect an edit made seconds ago, which is why logmonitor.load() reads
                # the file every time instead of caching it.
                data = logmonitor.describe()
                body = (
                    f"interval: {data['intervalSeconds']}s\n"
                    f"revision: {data['revision']}\n"
                    f"updated:  {data['updatedAt'] or '(never set - using default)'}\n"
                    f"bounds:   {data['minSeconds']}-{data['maxSeconds']}s "
                    f"step {data['stepSeconds']}s\n"
                    f"watching: {len(data['targets'])} target(s)\n"
                )
                for t in data["targets"]:
                    ident = t.get("ref") or t.get("path") or "(unknown)"
                    body += f"  - {t.get('kind')}: {ident}\n"
                body += (
                    "\nnotify the main agent only on: error in log, run terminal "
                    "(stopped/ended/completed), or a decision that needs the user."
                )
                return text_response("kaggle_log_monitor get", 0, body.rstrip(), "")

            if action == "set":
                if args.get("interval_seconds") is None:
                    return text_response(
                        "kaggle_log_monitor set", 2, "",
                        "action='set' needs interval_seconds",
                    )
                res = logmonitor.set_interval(args["interval_seconds"])
                note = []
                if res["clamped"]:
                    note.append(
                        f"requested {res['requestedSeconds']}s was clamped into "
                        f"{res['minSeconds']}-{res['maxSeconds']}s"
                    )
                if res["snapped"]:
                    note.append(f"rounded to the nearest {logmonitor.STEP_SECONDS}s step")
                body = (
                    f"{res['effective']}\n\n"
                    f"  previous: {res['previousSeconds']}s\n"
                    f"  now:      {res['intervalSeconds']}s\n"
                    f"  revision: {res['revision']}\n"
                    f"  file:     {res['path']}\n"
                )
                if note:
                    body += "\n" + "\n".join(f"note: {n}" for n in note) + "\n"
                if not res["changed"]:
                    body += "\nthe value was unchanged, so no monitoring subagent will see a change.\n"
                return text_response("kaggle_log_monitor set", 0, body, "")

            if action == "status":
                data = logmonitor.describe()
                body = (
                    f"config:     {data['path']} "
                    f"({'exists' if data['exists'] else 'not created yet - defaults in use)'}\n"
                    f"interval:   {data['intervalSeconds']}s "
                    f"(allowed {data['minSeconds']}-{data['maxSeconds']}, step {data['stepSeconds']}s)\n"
                    f"revision:   {data['revision']}\n"
                    f"updated:    {data['updatedAt'] or '(never)'}\n"
                    f"targets:    {len(data['targets'])}\n"
                )
                for t in data["targets"]:
                    body += f"  - {t.get('kind')}: {t.get('ref') or t.get('path')}\n"
                flags = data["notify"]
                body += (
                    "notify on:  "
                    f"error={flags['onError']}, terminal={flags['onTerminal']}, "
                    f"decision={flags['onDecision']}\n"
                    "\na monitoring subagent re-reads this file every cycle, so an edit applies\n"
                    "on the next poll without restarting the subagent."
                )
                return text_response("kaggle_log_monitor status", 0, body, "")

            if action == "target":
                res = logmonitor.set_target(
                    str(args.get("kind") or "kaggle"),
                    ref=str(args.get("ref") or ""),
                    path=str(args.get("path") or ""),
                )
                body = (
                    f"watching {len(res['targets'])} target(s):\n"
                    + "\n".join(
                        f"  - {t.get('kind')}: {t.get('ref') or t.get('path')}" for t in res["targets"]
                    )
                )
                return text_response("kaggle_log_monitor target", 0, body, "")

            if action == "clear":
                res = logmonitor.clear_targets()
                return text_response(
                    "kaggle_log_monitor clear", 0,
                    f"removed {res['removed']} target(s); no log is being watched now", "",
                )

            if action == "reset":
                res = logmonitor.reset()
                return text_response(
                    "kaggle_log_monitor reset", 0,
                    f"reset to defaults: interval {res['config']['intervalSeconds']}s, "
                    f"no targets, revision 0\n{res['path']}",
                    "",
                )
        except ValueError as exc:
            return text_response(f"kaggle_log_monitor {action}", 2, "", str(exc))

        return text_response(
            "kaggle_log_monitor", 2, "",
            f"unknown action: {action} (use get, set, status, target, clear or reset)",
        )

    if name == "kaggle_presence":
        action = str(args.get("action") or "get")
        try:
            if action == "get":
                data = presence.describe()
                verdict = graphstate.decide()
                eng = graphstate.graph_state().get("state", {}).get("search-engine", {})
                body = (
                    f"mode: {data['mode']}\n"
                    f"auto-advance budget: {data['autoDecisionsUsed']}/{data['autoAdvanceBudget']} "
                    f"used ({data['remaining']} left)\n"
                    f"changed at: {data['changedAt'] or 'never changed - default is present'}"
                    + ("\nnote: " + str(data["note"]) if data.get("note") else "")
                    + ("\nWARNING: away has been set for over 12h; confirm it is still true."
                       if data["stale"] else "")
                    + (f"\ndiscovery-search engine: {eng.get('value')} "
                       f"(ask which engine: kaggle_search_engine action=\"ask\")"
                       if eng.get("ok") else "")
                    + f"\n\nASK OR AUTO: {verdict['decision'].upper()}"
                      f"  (mode={verdict.get('mode')}, confidence={verdict['confidence']})\n"
                      f"{verdict['reason']}"
                    + (f"\n{verdict['warning']}" if verdict.get("warning") else "")
                    + (f"\nrecord auto-decisions with: {verdict['recordWith']}"
                       if verdict.get("recordWith") else "")
                    + f"\n\n{data['guidance']}"
                )
                return text_response("kaggle_presence get", 0, body, "")

            if action == "set":
                if not args.get("mode"):
                    return text_response(
                        "kaggle_presence set", 2, "",
                        "action='set' needs mode ('present' or 'away')",
                    )
                res = presence.set_mode(str(args["mode"]), note=args.get("note"))
                body = (
                    f"presence: {res['previousMode']} -> {res['mode']}\n\n"
                    f"{res['effective']}\n\n"
                    f"auto-advance budget: {res['autoDecisionsUsed']}/"
                    f"{res['autoAdvanceBudget']} used\n"
                    f"config: {res['path']}"
                )
                if not res["changed"]:
                    body += "\n\nthe mode was already that value, so nothing will change."
                return text_response("kaggle_presence set", 0, body, "")

            if action == "record":
                if not args.get("decision"):
                    return text_response(
                        "kaggle_presence record", 2, "",
                        "action='record' needs decision (the call you made on your own)",
                    )
                res = presence.record_auto_decision(
                    str(args["decision"]), rationale=str(args.get("rationale") or "")
                )
                if not res["ok"]:
                    return text_response(
                        "kaggle_presence record", 3,
                        "",
                        f"STOP: {res['reason']}\n\n"
                        "Do not continue unattended. Summarise what was done and what you would "
                        "do next, and leave the decision to the user.",
                    )
                body = (
                    f"recorded: {res['decision']}\n"
                    f"auto-advance budget: {res['autoDecisionsUsed']}/"
                    f"{res['autoAdvanceBudget']} used ({res['remaining']} left)"
                )
                if res["remaining"] <= 2:
                    body += (
                        "\nnote: the budget is nearly spent. Prefer reversible, low-cost "
                        "actions from here."
                    )
                return text_response("kaggle_presence record", 0, body, "")

            if action == "status":
                data = presence.describe()
                state = graphstate.graph_state()
                verdict = graphstate.decide()
                lines = [
                    "GRAPH STATE  (declared in skills/relationships.json, read live)",
                    f"graph: {state.get('graph') or '(not found)'}",
                    "",
                ]
                for name, entry in (state.get("state") or {}).items():
                    if entry.get("ok"):
                        lines.append(
                            f"  {name}: {entry['value']}   "
                            f"(possible: {', '.join(entry.get('values') or [])})"
                        )
                        lines.append(f"      read:  {entry.get('readTool')}")
                        lines.append(f"      write: {entry.get('writeTool')}")
                    else:
                        lines.append(f"  {name}: UNREADABLE - {entry.get('error')}")
                lines += [
                    "",
                    f"presence: {data['mode']}"
                    + (f"  (budget {data['autoDecisionsUsed']}/{data['autoAdvanceBudget']}, "
                       f"{data['remaining']} left)" if data["mode"] == "away" else ""),
                    f"changed:  {data['changedAt'] or 'never (default is present)'}",
                    f"config:   {data['path']}",
                    "",
                    f"ASK OR AUTO: {verdict['decision'].upper()}  "
                    f"(confidence={verdict['confidence']})",
                    f"  {verdict['reason']}",
                ]
                if verdict.get("warning"):
                    lines.append(f"  WARNING: {verdict['warning']}")
                if verdict.get("recordWith"):
                    lines.append(f"  record auto-decisions with: {verdict['recordWith']}")
                lines.append(f"  {data['guidance']}")
                return text_response("kaggle_presence status", 0, "\n".join(lines), "")

            if action == "reset":
                res = presence.reset_budget(args.get("budget"))
                return text_response(
                    "kaggle_presence reset", 0,
                    f"away budget refilled: {res['config']['autoAdvanceBudget']} auto-decisions\n"
                    f"{res['path']}",
                    "",
                )
        except ValueError as exc:
            return text_response(f"kaggle_presence {action}", 2, "", str(exc))

        return text_response(
            "kaggle_presence", 2, "",
            f"unknown action: {action} (use get, set, record, status or reset)",
        )

    if name == "kaggle_experiment_tree":
        action = str(args.get("action") or "read")
        comp = str(args.get("competition") or "").strip()
        if not comp:
            return text_response(
                f"kaggle_experiment_tree {action}", 2, "",
                "competition is required - it identifies which tree",
            )

        if action == "read":
            tree = experiment_tree.read(comp)
            problems = tree.get("problems") or []
            body = experiment_tree.plan_prompt(comp)
            if problems:
                body += "\n\nSTRUCTURAL PROBLEMS (the tree is not trustworthy as it stands):\n" + \
                    "\n".join(f"  - {p}" for p in problems)
            return text_response(
                "kaggle_experiment_tree read", 0, body,
                "",
            )

        if action == "plan":
            return text_response(
                "kaggle_experiment_tree plan", 0, experiment_tree.plan_prompt(comp), "",
            )

        if action == "select":
            sel = experiment_tree.select_next(
                experiment_tree.load(comp), weights=args.get("weights"))
            if not sel.get("ok"):
                return text_response(
                    "kaggle_experiment_tree select", 3, "", sel.get("reason", "nothing to select"))
            lines = [
                f"recommended parent: {sel['recommendedParent']}",
                f"  {sel['note']}",
                "",
                f"batch (|C| <= workers={sel['workers']}): {', '.join(sel['batch'])}",
                "",
                f"weights: {sel['weights']}  (cooling half-life {sel['coolingHalfLife']} visits)",
                f"confidence: {sel['confidence']}  ({sel['familyCount']} method families)",
            ]
            if sel.get("confidenceNote"):
                lines.append(f"  {sel['confidenceNote']}")
            lines += [
                "",
                f"{'node':<6}{'utility':<10}{'score':<9}{'scoreN':<9}{'progN':<8}"
                f"{'nov':<6}{'visits':<8}{'effCost':<10}{'family':<12}operator",
            ]
            for c in sel["candidates"]:
                noise = " (delta within noise)" if c.get("withinNoise") else ""
                lines.append(
                    f"{c['id']:<6}{c['utility']:<10}{str(c['score']):<9}{c['scoreNorm']:<9}"
                    f"{c['progressNorm']:<8}{c['novelty']:<6}{c['visits']:<8}"
                    f"{c.get('effectiveCost', 0):<10}{str(c['family']):<12}{c['operator']}{noise}"
                )
                # Regressions are reported here, loudly and next to the score. This never blocks
                # anything - judging the trade-off is the agent's call - but a composite that
                # hides a real drop is exactly the failure this exists to make visible.
                for r in c.get("regressedCriteria") or []:
                    lines.append(
                        f"       REGRESSED vs parent: {r['name']} {r['from']} -> {r['to']}"
                        + (f" (std {r['std']})" if r.get("std") is not None else "")
                        + "  -- write this into the node's reason"
                    )
            lines += [
                "",
                "This is deliberately NOT 'expand the highest score'. Quality leads, but a node",
                "that made real progress or opened a new method family can outrank the incumbent,",
                "and an over-expanded node is damped so one branch cannot monopolise the budget.",
                "Adapting arXiv 2607.28568 sec. 5.2; its matched comparison moved Medal Average",
                "from 53.03% to 60.61% over score-greedy AIRA-Evo under the same model and budget.",
            ]
            return text_response("kaggle_experiment_tree select", 0, "\n".join(lines), "")

        if action == "board":
            board = experiment_tree.experience_board(experiment_tree.load(comp))
            lines = [
                f"method families: {board['familyCount']}",
                "",
                f"{'family':<14}{'nodes':<7}{'kept':<6}{'refuted':<9}{'best':<9}bestNode",
            ]
            for f in board["families"]:
                lines.append(
                    f"{f['family']:<14}{f['nodes']:<7}{f['kept']:<6}{f['refuted']:<9}"
                    f"{str(f['best']):<9}{f['bestNode']}"
                )
            lines += ["", "gain by operator:", "  " + str(board["operatorGain"])]
            efc = board.get("efc") or {}
            lines += ["", "effective feedback compute (EFC, per criterion, never merged):"]
            lines.append(
                f"  raw={efc.get('rawQuotaHours')}h  effective={efc.get('effectiveQuotaHours')}h  "
                f"wasted={efc.get('wastedQuotaHours')}h"
                + (f"  ({efc['wastedFraction']:.0%} of raw bought nothing)"
                   if isinstance(efc.get("wastedFraction"), (int, float)) else "")
            )
            for name, flags in sorted((efc.get("perCriterion") or {}).items()):
                lines.append(f"    {name}: " + ", ".join(
                    f"{k}={v}" for k, v in flags.items() if v))
            lines.append(f"  {efc.get('note', '')}")
            layers = board.get("failureLayers") or {}
            lines += ["", "refuted by harness layer (over 60% of measured harness failures were"
                      " output-contract or tool-recovery, not reasoning):"]
            if layers:
                lines += [f"  {k}: {v}" for k, v in sorted(layers.items())]
            else:
                lines.append("  (none yet)")
            if board["refutedByFamily"]:
                lines += ["", "refuted, by family (never repeat these):"]
                for fam, ids in board["refutedByFamily"].items():
                    lines.append(f"  {fam}: {', '.join(ids)}")
            lines += ["", board["operatorNote"]]
            return text_response("kaggle_experiment_tree board", 0, "\n".join(lines), "")

        if action == "replay":
            doc = experiment_tree.load(comp)
            policy = args.get("policy")
            policies = policy if isinstance(policy, list) else [policy]
            policies = [p for p in policies if isinstance(p, dict)]
            if not policies:
                return text_response(
                    "kaggle_experiment_tree replay", 2, "",
                    "action='replay' needs policy (an object with params)",
                )
            rounds = _replay_worlds(doc, args.get("rounds"))
            if not rounds:
                return text_response(
                    "kaggle_experiment_tree replay", 3, "",
                    "no archived round to replay over. Close a round first "
                    "(action=\"round_close\") - replay works on completed history, which is the "
                    "point: it is free because nothing has to be re-run.",
                )
            out = []
            for p in policies:
                r = experiment_tree.replay(rounds[-1]["tree"], p.get("params") or p)
                out.append({
                    "id": p.get("id") or p.get("label"),
                    "label": p.get("label"),
                    "V": round(r["V"], 6), "N": r["N"], "k": r["k"],
                    "effectiveCost": round(r["effectiveCost"], 4),
                    "costDataIncomplete": r["costDataIncomplete"],
                    "revealed": r["revealed"],
                })
            body = ["replayed over the most recent archived round at ZERO execution cost:",
                    f"  round: {rounds[-1].get('round')}  "
                    f"(compliant with the node rules: {rounds[-1].get('compliant')})", ""]
            for o in out:
                body.append(
                    f"  {o['id']}: V={o['V']}  N={o['N']}  k={o['k']}  "
                    f"effectiveCost={o['effectiveCost']}"
                )
                if o["costDataIncomplete"]:
                    body.append(
                        "    note: some revealed nodes predate cost/criteria, so the cost term "
                        "falls back to counting attempts - treat betaCost as approximate here."
                    )
                if not rounds[-1].get("compliant"):
                    body.append(
                        "    note: this round was archived before the node-shape rules, so the "
                        "simulator sample is biased toward older, less complete runs."
                    )
            body += ["", "revealed per round (checkable by hand):"]
            for o in out:
                body.append(f"  {o['id']}: {o['revealed']}")
            return text_response("kaggle_experiment_tree replay", 0, "\n".join(body), "")

        if action == "compare":
            doc = experiment_tree.load(comp)
            policies = args.get("policy")
            candidates = policies if isinstance(policies, list) else []
            candidates = [c for c in candidates if isinstance(c, dict)]
            if not candidates:
                return text_response(
                    "kaggle_experiment_tree compare", 2, "",
                    "action='compare' needs policy as a LIST of candidate policies",
                )
            rounds = _replay_worlds(doc, args.get("rounds"))
            if not rounds:
                return text_response("kaggle_experiment_tree compare", 3, "",
                                    "no archived round to compare over; close a round first")
            deployed_id = doc.get("deployedPolicy")
            deployed = (doc.get("policies") or {}).get(deployed_id)
            if not any(c.get("id") == deployed_id for c in candidates):
                if deployed:
                    candidates = [{"id": deployed_id, "label": deployed.get("label"),
                                   "params": deployed.get("params")}] + candidates
                else:
                    return text_response(
                        "kaggle_experiment_tree compare", 3, "",
                        "no policy is deployed, so there is nothing to be monotone against. "
                        "Create and deploy one first (action=\"policy\").",
                    )
            res = experiment_tree.compare(rounds[-1]["tree"], candidates, deployed=deployed)
            if not res.get("ok"):
                return text_response("kaggle_experiment_tree compare", 3, "",
                                    f"{res.get('message')}")
            lines = [
                f"{'policy':<16}{'V':<12}{'N':<6}{'k':<6}effectiveCost",
            ]
            for r in res["results"]:
                mark = "  (current)" if r["isCurrent"] else ""
                lines.append(
                    f"{str(r['id']):<16}{r['V']:<12.6f}{r['N']:<6}{r['k']:<6}"
                    f"{r['effectiveCost']}{mark}"
                )
            lines += ["", f"best: {res['best']['id']}", res["guarantee"]]
            return text_response("kaggle_experiment_tree compare", 0, "\n".join(lines), "")

        if action == "policy":
            sub = str(args.get("policy_action") or args.get("sub") or "list")
            if sub == "create":
                res = experiment_tree.register_policy(
                    comp, args.get("params") or {}, label=str(args.get("label") or ""),
                    note=str(args.get("note") or ""))
                if not res.get("ok"):
                    return text_response("kaggle_experiment_tree policy create", 2, "",
                                        res.get("message", ""))
                p = res["policy"]
                return text_response(
                    "kaggle_experiment_tree policy create", 0,
                    f"registered {p['id']} ({p['label']}); parent={p['parent']}\n"
                    f"params: {p['params']}\n"
                    f"registering does NOT deploy it. Use action=\"policy\" "
                    f"policy_action=\"deploy\" policy_id=\"{p['id']}\" for that.",
                    "")
            if sub == "deploy":
                pid = str(args.get("policy_id") or "")
                res = experiment_tree.deploy_policy(comp, pid)
                if not res.get("ok"):
                    return text_response("kaggle_experiment_tree policy deploy", 2, "",
                                        res.get("message", ""))
                return text_response(
                    "kaggle_experiment_tree policy deploy", 0,
                    f"deployed {pid} (was {res['previous']})\n{res['note']}", "")
            doc = experiment_tree.load(comp)
            pol = doc.get("policies") or {}
            if not pol:
                return text_response("kaggle_experiment_tree policy list", 0,
                                    "no policies registered yet", "")
            lines = [f"deployed: {doc.get('deployedPolicy')}", ""]
            for pid in sorted(pol):
                p = pol[pid]
                mark = "  <- deployed" if pid == doc.get("deployedPolicy") else ""
                lines.append(f"  {pid}  {p.get('label')}{mark}")
                lines.append(f"      {p.get('params')}")
            return text_response("kaggle_experiment_tree policy list", 0, "\n".join(lines), "")

        if action == "round_close":
            doc = experiment_tree.load(comp)
            res = experiment_tree.close_round(
                comp, policy_id=doc.get("deployedPolicy"),
                read_revision=args.get("read_revision"))
            if not res.get("ok"):
                return text_response(f"kaggle_experiment_tree {action}", 3, "",
                                    res.get("message", ""))
            return text_response(
                "kaggle_experiment_tree round_close", 0,
                f"archived {res['archived']} node(s) as round {res['currentRound'] - 1}\n"
                f"rounds in the replay pool: {res['rounds']}\n"
                f"now on round {res['currentRound']} with an empty tree\n"
                "the archived round is what action=\"replay\" walks; that is why it is free.", "")

        if action == "anchor":
            if args.get("held_out"):
                res = experiment_tree.declare_anchor(
                    comp, str(args["held_out"]), rule=str(args.get("rule") or ""))
                return text_response(
                    "kaggle_experiment_tree anchor declare", 0,
                    f"held-out set: {res['anchor']['heldOut']}\nrule: {res['anchor']['rule']}\n"
                    "from now on a node whose metric.split or rankSource names this set is "
                    "rejected. Declare it BEFORE experimenting, not after.", "")
            st = experiment_tree.status(comp)
            a = st.get("anchor") or {}
            if not a.get("declared"):
                return text_response(
                    "kaggle_experiment_tree anchor status", 0,
                    "no anchor declared. Without one, a replay score is only a re-ranking of "
                    "data the search has already seen.", "")
            return text_response(
                "kaggle_experiment_tree anchor status", 0,
                f"held-out: {a.get('heldOut')}\nrule: {a.get('rule')}\ndeclared: {a.get('declaredAt')}",
                "")

        if action == "undo":
            res = experiment_tree.undo(comp)
            if not res.get("ok"):
                return text_response("kaggle_experiment_tree undo", 3, "", res.get("message", ""))
            return text_response(
                "kaggle_experiment_tree undo", 0,
                f"undid {res['undone']}\nrevision: {res['revision']}", "")

        if action == "analyze":
            res = experiment_tree.analyze(comp)
            if not res.get("produced"):
                detail = "; ".join(f"{s['kind']}: {s.get('reason')}" for s in res.get("skipped") or [])
                return text_response(
                    "kaggle_experiment_tree analyze", 3, "",
                    "nothing could be plotted yet" + (f" ({detail})" if detail else "")
                    + ". Record at least two scored nodes first.")
            lines = [f"plotted {len(res['produced'])} figure(s) for {comp}:"]
            for p in res["produced"]:
                lines.append(f"  [{p['kind']}] {p['what']}")
                lines.append(f"      {p['path']}")
            if res.get("skipped"):
                lines.append("")
                lines.append("skipped, and why:")
                for s in res["skipped"]:
                    lines.append(f"  [{s['kind']}] {s.get('reason')}")
            lines += ["", res["note"]]
            return text_response("kaggle_experiment_tree analyze", 0, "\n".join(lines), "")

        if action == "review":
            r = experiment_tree.review(comp)
            lines = [f"review of {comp} at revision {r['revision']}, base {r['base'] or '(none)'}", ""]
            lines.append(f"KEPT ({len(r['kept'])}):")
            for e in r["kept"]:
                lines.append(f"  {e['id']} [{e['operator']}/{e['family']}] delta={e['delta']}"
                             f"  {str(e['change'])[:56]}")
                for b in e.get("backedBy") or []:
                    q = str(b.get("quote") or "")[:100]
                    lines.append(f"      <- {b['relation']} {b['sourceId']} "
                                 f"({b.get('licence')}): {q or '(no quote stored)'}")
                if e.get("evidence") == "local-only":
                    lines.append("      <- evidence: local-only (no citation)")
            lines += ["", f"REFUTED ({len(r['refuted'])}):"]
            for e in r["refuted"]:
                lines.append(f"  {e['id']} [{e.get('failureLayer')}] {str(e['change'])[:48]}")
                lines.append(f"      {e['reason']}")
            if r["failuresByLayer"]:
                lines += ["", "failures by harness layer:"]
                for layer, ids in r["failuresByLayer"].items():
                    lines.append(f"  {layer}: {', '.join(ids)}")
            lines += ["", f"RESEARCH NODES ({len(r['research'])}):"]
            for e in r["research"]:
                lines.append(f"  {e['id']}: {str(e['change'])[:58]}")
            cov = r["coverage"]
            lines += ["",
                      f"coverage: {cov['withSources']}/{cov['scoredNodes']} concluded nodes backed "
                      f"by a source, {cov['declaredLocalOnly']} declared local-only, "
                      f"{cov['unsupported']} neither"]
            if r["linksWithoutQuote"]:
                lines.append("links with no stored quote (uncheckable): "
                             + ", ".join(r["linksWithoutQuote"]))
            lines += ["", "next questions the tree raises:"]
            for q in r["nextQuestions"]:
                lines.append(f"  - {q}")
            lines += ["", r["note"]]
            return text_response("kaggle_experiment_tree review", 0, "\n".join(lines), "")

        if action == "status":
            st = experiment_tree.status(comp)
            lines = [
                f"tree:     {st['path']} ({'exists' if st['exists'] else 'not created yet'})",
                f"revision: {st['revision']}   nodes: {st['nodes']}  "
                f"(kept {st['kept']}, refuted {st['refuted']}, research {st['research']})",
                f"base:     {st['base'] or '(none yet)'}",
                f"sound:    {'yes' if st['sound'] else 'NO'}",
                "",
                f"round:    {st['currentRound']}   archived rounds: {st['rounds']}",
                f"policy:   deployed={st['deployedPolicy'] or '(none)'}   "
                f"registered={', '.join(st['policies']) or '(none)'}",
            ]
            anchor = st.get("anchor") or {}
            lines.append(
                "anchor:   "
                + (f"held-out = {anchor.get('heldOut')}" if anchor.get("declared")
                   else "NOT DECLARED")
            )
            efc = st.get("efc") or {}
            if efc.get("rawQuotaHours"):
                frac = efc.get("wastedFraction")
                lines.append(
                    f"compute:  raw={efc['rawQuotaHours']}h  effective={efc['effectiveQuotaHours']}h  "
                    + (f"wasted={frac:.0%}" if isinstance(frac, (int, float)) else "wasted=n/a")
                )
            if st.get("failureLayers"):
                lines.append("failures: " + ", ".join(
                    f"{k}={v}" for k, v in sorted(st["failureLayers"].items())))
            if not anchor.get("declared"):
                lines.append(
                    "\nno evaluation anchor is declared, so a replay score is currently only a "
                    "re-ranking of data the search has already seen. Declare one with "
                    "action=\"anchor\" before the next round."
                )
            if st["problems"]:
                lines.append("\nproblems:")
                lines += [f"  - {p}" for p in st["problems"]]
            lines += ["", st["staticChecksOnly"]]
            return text_response("kaggle_experiment_tree status", 0, "\n".join(lines), "")

        if action == "record":
            node = args.get("node")
            if not isinstance(node, dict):
                return text_response(
                    "kaggle_experiment_tree record", 2, "",
                    "action='record' needs node (an object describing the one node)",
                )
            rev = args.get("read_revision")
            res = experiment_tree.record(
                comp, node,
                read_revision=int(rev) if rev is not None else None,
                new_base=args.get("new_base"),
            )
            if not res.get("ok"):
                detail = ""
                if res.get("problems"):
                    detail = "\n" + "\n".join(f"  - {p}" for p in res["problems"])
                tree_part = ""
                if res.get("tree"):
                    t = res["tree"]
                    tree_part = (
                        f"\n\ncurrent tree: revision {t.get('revision')}, base "
                        f"{(t.get('base') or {}).get('id') or '(none)'}, "
                        f"{len(t.get('nodes') or {})} nodes"
                    )
                return text_response(
                    f"kaggle_experiment_tree record ({res.get('code')})", 3,
                    "", f"{res.get('message')}{detail}{tree_part}",
                )
            body = (
                f"recorded {res['nodeId']} ({res['kind']})\n"
                f"revision: {res['revision']}\n"
                f"base: {res['base'] or '(unchanged)'}\n"
                f"tree: {res['path']}\n\n"
                f"{res['note']}"
            )
            return text_response("kaggle_experiment_tree record", 0, body, "")

        if action == "consider":
            change = str(args.get("change") or "")
            if not change:
                return text_response(
                    "kaggle_experiment_tree consider", 2, "",
                    "action='consider' needs change (what you are about to do, in one line)",
                )
            res = experiment_tree.consider(
                comp, change,
                hypothesis=str(args.get("hypothesis") or ""),
                operator=str(args.get("operator") or ""),
                family=str(args.get("family") or ""),
            )
            if not res.get("ok"):
                return text_response(
                    f"kaggle_experiment_tree consider ({res.get('code')})", 3, "",
                    f"{res.get('message')}",
                )
            lines = [
                f"verdict: {res['verdict']}   (worth a node: {res['worthANode']})",
                f"base: {res.get('base') or '(none)'}   in flight: "
                f"{', '.join(res.get('inFlight') or []) or '(none)'}",
                "",
                res["why"],
            ]
            m = res.get("match")
            if m:
                lines += [
                    "",
                    f"matched {m['id']} (score {m['score']}, {m.get('kind')}, "
                    f"verdict {m.get('verdict') or 'pending'})",
                    f"  change: {m.get('change')}",
                    f"  reason: {m.get('reason')}",
                ]
            others = [x for x in (res.get("matches") or []) if not m or x["id"] != m["id"]]
            if others:
                lines += ["", "other nodes that overlap: "
                              + ", ".join(f"{x['id']} ({x['score']})" for x in others)]
            return text_response("kaggle_experiment_tree consider", 0, "\n".join(lines), "")

        if action == "prune":
            res = experiment_tree.prune(
                comp, str(args.get("node") or ""), str(args.get("reason") or ""),
                read_revision=(int(args["read_revision"])
                               if args.get("read_revision") is not None else None),
            )
            if not res.get("ok"):
                detail = ""
                if res.get("problems"):
                    detail = "\n" + "\n".join(f"  - {p}" for p in res["problems"])
                return text_response(
                    f"kaggle_experiment_tree prune ({res.get('code')})", 3, "",
                    f"{res.get('message')}{detail}",
                )
            return text_response(
                "kaggle_experiment_tree prune", 0,
                f"pruned {res['pruned']}\nreason: {res['reason']}\n"
                f"revision: {res['revision']}\nundo restores it",
                "",
            )

        if action in ("declare", "settle"):
            node = args.get("node")
            if not isinstance(node, dict):
                return text_response(
                    f"kaggle_experiment_tree {action}", 2, "",
                    f"action={action!r} needs node (an object describing the node)",
                )
            rev = args.get("read_revision")
            if action == "declare":
                res = experiment_tree.declare(
                    comp, node, read_revision=int(rev) if rev is not None else None)
            else:
                declared = str(args.get("declared") or "").strip()
                if not declared:
                    return text_response(
                        "kaggle_experiment_tree settle", 2, "",
                        "action='settle' needs declared (the id of the declaration this result "
                        "settles)",
                    )
                res = experiment_tree.settle(
                    comp, declared, node,
                    read_revision=int(rev) if rev is not None else None)
            if not res.get("ok"):
                detail = ""
                if res.get("problems"):
                    detail = "\n" + "\n".join(f"  - {p}" for p in res["problems"])
                return text_response(
                    f"kaggle_experiment_tree {action} ({res.get('code')})", 3,
                    "", f"{res.get('message')}{detail}",
                )
            verb = "declared" if action == "declare" else "settled"
            extra = ""
            if action == "declare":
                extra = ("\nlaunch it with "
                         f'kaggle_kernel_launch declares="{res["nodeId"]}"')
            else:
                pending = experiment_tree.pending_declarations(comp)
                if pending:
                    extra = ("\nstill awaiting a result: "
                             + ", ".join(p["id"] for p in pending))
                else:
                    extra = "\nno declarations are awaiting a result."
            body = (
                f"{verb} {res['nodeId']}\n"
                f"revision: {res['revision']}\n"
                f"base: {res['base'] or '(unchanged)'}\n"
                f"tree: {res['path']}\n{extra}\n\n{res['note']}"
            )
            return text_response(f"kaggle_experiment_tree {action}", 0, body, "")

        return text_response(
            "kaggle_experiment_tree", 2, "",
            f"unknown action: {action} (use read, declare, settle, record, plan or status)",
        )

    if name == "kaggle_search_engine":
        action = str(args.get("action") or "ask")

        if action == "ask":
            # The whole point of this action: it is called BEFORE a discovery search and it
            # returns the presence mode, so the agent knows whether the engine is a question
            # for the user or a default to record. Reading presence here rather than trusting
            # the agent to remember is what makes the tiering stick.
            pres = presence.describe()
            cur = searchengine.describe()
            options = []
            for key in sorted(searchengine.ENGINES):
                spec = searchengine.ENGINES[key]
                options.append(
                    f"  - {key}: {spec['label']} 闂?{spec['note']}"
                    + ("   (current)" if key == cur["engine"] else "")
                )
            lines = [
                "DISCOVERY SEARCH 闂?choose an engine before searching.",
                "",
                "Is this a discovery search (you do not have the URL yet)?",
                "  yes  -> an engine is required: use this tool, then open the returned URL.",
                "  no   -> you already have the URL. Open it directly; this tool is EXEMPT and",
                "          asking which engine to use to open a known URL is not a real question.",
                "",
                f"presence: {pres['mode']}"
                + (f" (budget {pres['autoDecisionsUsed']}/{pres['autoAdvanceBudget']})"
                   if pres["mode"] == "away" else ""),
                "",
                "engines:",
                *options,
                "",
            ]
            if pres["mode"] == "away":
                lines += [
                    f"YOU ARE AWAY. Do not ask. Use '{cur['engine']}', then record it:",
                    '  kaggle_presence action="record"',
                    f'    decision="discovery search on {cur["label"]} without asking"',
                    '    rationale="user is away; the engine choice is reversible and changes no external state"',
                ]
                if pres["remaining"] <= 2:
                    lines.append(
                        "note: the away budget is nearly spent; if action='record' refuses, stop "
                        "and wait rather than searching on your own judgement."
                    )
            else:
                lines += [
                    "YOU ARE PRESENT. Ask the user which engine, then apply their answer:",
                    f'  kaggle_search_engine action="use" engine="<bing|google>"',
                    "If they already named an engine this turn, skip the question and just apply it.",
                ]
            body = "\n".join(lines)

            q = str(args.get("query") or "").strip()
            if q:
                try:
                    built = searchengine.search_url(q)
                    body += (
                        f"\n\nfor query {q!r} the current engine would give:\n  "
                        f"{built['url']}\n  read with: query(kind=\"text\", "
                        f"selector={built['resultsSelector']})"
                    )
                except ValueError as exc:
                    body += f"\n\n(no URL built: {exc})"
            return text_response("kaggle_search_engine ask", 0, body, "")

        if action == "use":
            if not args.get("engine"):
                return text_response(
                    "kaggle_search_engine use", 2, "",
                    "action='use' needs engine",
                )
            res = searchengine.set_engine(
                str(args["engine"]), language=args.get("language")
            )
            body = (
                f"engine: {res['previousEngine']} -> {res['engine']} ({res['label']})"
                + ("" if res["changed"] else "   (unchanged)")
                + f"\nnote: {searchengine.ENGINES[res['engine']]['note']}"
                + f"\nconfig: {res['path']}"
            )
            q = str(args.get("query") or "").strip()
            if q:
                built = searchengine.search_url(q)
                body += (
                    f"\n\nurl: {built['url']}"
                    f"\nread: query(kind=\"text\", selector={built['resultsSelector']})"
                    f"\n{built['readingRule']}"
                )
            return text_response("kaggle_search_engine use", 0, body, "")

        if action == "describe":
            d = searchengine.describe()
            body = (
                f"engine: {d['engine']} ({d['label']})\n"
                f"note:    {d['note']}\n"
                f"language: {d['language']}   source: {d['source']}\n"
                f"changed: {d['changedAt'] or 'never - using the default'}\n"
                f"config:  {d['path']} ({'exists' if d['exists'] else 'not created yet'})\n"
                f"available: {', '.join(d['available'])}"
            )
            return text_response("kaggle_search_engine describe", 0, body, "")

        return text_response(
            "kaggle_search_engine", 2, "",
            f"unknown action: {action} (use ask, use or describe)",
        )

    if name == "kaggle_sources":
        action = str(args.get("action") or "list")

        if action == "doctor":
            p = deps.probe()
            types = deps.available_chart_types()
            lines = [
                f"plotting is ready now: {p['bundled']['engine']}",
                f"  chart types available: {', '.join(types['all'])}",
                f"  python {p['python']} at {p['executable']}",
                "",
            ]
            if p["present"]:
                lines.append("optional, present:")
                for n, meta in p["present"].items():
                    lines.append(f"  {n} {meta['version']} - unlocks "
                                 f"{', '.join(meta['unlocks'])}")
            if p["missing"]:
                lines += ["", "optional, absent (plotting is unaffected):"]
                for n, meta in p["missing"].items():
                    lines.append(f"  {n} - would give {', '.join(meta['unlocks'])} "
                                 f"[~{meta['sizeHint']}]")
                    lines.append(f"      {meta['why']}")
            lines += [
                "",
                "Nothing here is required to draw a figure, and nothing is installed unless the "
                "user explicitly agrees. If they want the extra chart types: "
                "kaggle_sources action=\"install\" packages=[...]",
            ]
            return text_response("kaggle_sources doctor", 0, "\n".join(lines), "")

        if action == "install":
            pkgs = args.get("packages")
            if not isinstance(pkgs, list) or not pkgs:
                return text_response(
                    "kaggle_sources install", 2, "",
                    "action='install' needs packages=[...]. Do not call this to 'just try it' - "
                    "ask the user first; it changes their Python environment.",
                )
            res = deps.install([str(x) for x in pkgs])
            if not res.get("ok"):
                return text_response("kaggle_sources install", 3, "", res.get("message", ""))
            return text_response("kaggle_sources install", 0,
                                 f"installed: {', '.join(res['installed']) or '(none)'}\n"
                                 f"already present: {', '.join(res['alreadyPresent']) or '(none)'}\n"
                                 + res.get("note", ""), "")

        if action == "add":
            res = srclib.add(
                kind=str(args.get("kind") or "paper"),
                title=str(args.get("title") or ""),
                url=str(args.get("url") or ""),
                authors=str(args.get("authors") or ""),
                published=str(args.get("published") or ""),
                venue=str(args.get("venue") or ""),
                summary=str(args.get("summary") or ""),
                licence=str(args.get("licence") or ""),
                source_id=str(args.get("source_id") or ""),
                doi=str(args.get("doi") or ""),
                arxiv=str(args.get("arxiv") or ""),
                notes=str(args.get("notes") or ""),
            )
            if not res.get("ok"):
                return text_response("kaggle_sources add", 2, "", res.get("message", ""))
            s = res["source"]
            head = "already stored, not creating a rival record" if res.get("duplicate") else "stored"
            body = (f"{head}: {s['id']}  [{s['kind']}] {s['title']}\n"
                    f"url: {s.get('url') or '(none)'}\n"
                    f"licence: {s.get('licence')}   extracts: {len(s.get('extracts') or [])}\n"
                    f"path: {res['path']}")
            if s.get("licence") in ("unknown", "unspecified", "none"):
                body += ("\nnote: no licence recorded. That is a finding, not a blank - it decides "
                         "whether the work can be built on at all.")
            return text_response("kaggle_sources add", 0, body, "")

        if action == "extract":
            res = srclib.add_extract(
                str(args.get("source_id") or ""), str(args.get("quote") or ""),
                claim=str(args.get("claim") or ""), locator=str(args.get("locator") or ""))
            if not res.get("ok"):
                return text_response("kaggle_sources extract", 2, "", res.get("message", ""))
            return text_response(
                "kaggle_experiment_tree extract" if False else "kaggle_sources extract", 0,
                f"{res['sourceId']} now has {res['extracts']} extract(s)\n"
                f"recorded: {res['added']['quote'][:180]}\n"
                + (f"supports: {res['added']['claim']}" if res['added'].get('claim') else ""), "")

        if action == "link":
            res = srclib.link_to_node(
                str(args.get("competition") or ""), str(args.get("node_id") or ""),
                str(args.get("source_id") or ""), relation=str(args.get("relation") or "supports"),
                quote=str(args.get("quote") or ""))
            if not res.get("ok"):
                return text_response("kaggle_sources link", 2, "", res.get("message", ""))
            dup = " (already linked)" if res.get("duplicate") else ""
            return text_response(
                "kaggle_sources link", 0,
                f"node {res['nodeId']} {res['relation']}s {res['sourceId']}{dup}\n"
                f"source: {res.get('sourceTitle')}\n"
                f"node now has {res['links']} source link(s)", "")

        if action == "unlink":
            res = srclib.unlink(str(args.get("competition") or ""),
                                 str(args.get("node_id") or ""),
                                 str(args.get("source_id") or ""))
            if not res.get("ok"):
                return text_response("kaggle_sources unlink", 2, "", res.get("message", ""))
            return text_response("kaggle_sources unlink", 0,
                                 f"removed {res['removed']} link(s) of {res['sourceId']} "
                                 f"from {res['nodeId']}", "")

        if action == "get":
            rec = srclib.get(str(args.get("source_id") or ""))
            if not rec:
                return text_response("kaggle_sources get", 2, "",
                                    f"no source {args.get('source_id')!r}")
            bl = srclib.backlink(rec["id"])
            lines = [f"{rec['id']}  [{rec['kind']}] {rec['title']}",
                     f"url: {rec.get('url') or '(none)'}",
                     f"licence: {rec.get('licence')}   authors: {rec.get('authors') or '(none)'}",
                     f"published: {rec.get('published') or '(unknown)'}"]
            if rec.get("summary"):
                lines += ["", f"summary: {rec['summary']}"]
            if rec.get("extracts"):
                lines += ["", "extracts (the sentences that carry the claims):"]
                for e in rec["extracts"]:
                    lines.append(f"  - \"{e['quote'][:200]}\"")
                    if e.get("claim"):
                        lines.append(f"      supports: {e['claim']}")
            if bl.get("citedBy"):
                lines += ["", f"cited by {bl['citedBy']} node(s):"]
                for h in bl["hits"]:
                    lines.append(f"  {h['competition']}/{h['nodeId']} ({h['relation']})")
            return text_response("kaggle_sources get", 0, "\n".join(lines), "")

        if action in ("list", "search"):
            hits = srclib.search(query=str(args.get("query") or ""),
                                  kind=str(args.get("kind") or ""),
                                  needs_extract=bool(args.get("needs_extract")))
            if not hits:
                return text_response(f"kaggle_sources {action}", 0,
                                     "no stored source matches", "")
            lines = [f"{len(hits)} source(s):"]
            for h in hits:
                lines.append(f"  {h['id']:<8}[{h['kind']:<8}] {h['title'][:70]}")
                lines.append(f"           licence={h['licence']}  extracts={h['extracts']}  "
                             f"{h['url'] or ''}")
            return text_response(f"kaggle_sources {action}", 0, "\n".join(lines), "")

        if action == "backlink":
            res = srclib.backlink(str(args.get("source_id") or ""))
            if not res.get("ok"):
                return text_response("kaggle_sources backlink", 2, "", res.get("message", ""))
            if not res["citedBy"]:
                return text_response("kaggle_sources backlink", 0,
                                     f"{res['sourceId']} is stored but cited by no node yet", "")
            lines = [f"{res['sourceId']} - {res['title']}", f"cited by {res['citedBy']} node(s):"]
            for h in res["hits"]:
                lines.append(f"  {h['competition']}/{h['nodeId']} ({h['relation']}) "
                             f"verdict={h['verdict']}: {str(h['change'])[:60]}")
            return text_response("kaggle_sources backlink", 0, "\n".join(lines), "")

        if action == "coverage":
            comp = str(args.get("competition") or "")
            cov = srclib.coverage(comp)
            lines = [f"{comp}: {cov['withSources']}/{cov['scoredNodes']} concluded nodes are "
                     f"backed by a source"
                     + (f" ({cov['ratio']:.0%})" if cov.get("ratio") is not None else ""),
                     f"declared local-only: {cov['declaredLocalOnly']}",
                     f"unsupported (neither): {cov['unsupported']}"
                     + (f" -> {', '.join(cov['unsupportedNodeIds'][:8])}"
                        if cov["unsupportedNodeIds"] else ""),
                     "", cov["note"]]
            return text_response("kaggle_sources coverage", 0, "\n".join(lines), "")

        if action == "review":
            comp = str(args.get("competition") or "")
            r = experiment_tree.review(comp)
            lines = [f"review of {comp} at revision {r['revision']}, base {r['base'] or '(none)'}",
                     "", f"KEPT ({len(r['kept'])}):"]
            for e in r["kept"]:
                lines.append(f"  {e['id']} [{e['operator']}/{e['family']}] "
                             f"delta={e['delta']}  {str(e['change'])[:58]}")
                for b in e.get("backedBy") or []:
                    q = str(b.get("quote") or "")[:110]
                    lines.append(f"      <- {b['relation']} {b['sourceId']} "
                                 f"({b.get('licence')}): {q or '(no quote stored)'}")
                if e.get("evidence") == "local-only":
                    lines.append("      <- evidence: local-only (no citation)")
            lines += ["", f"REFUTED ({len(r['refuted'])}):"]
            for e in r["refuted"]:
                lines.append(f"  {e['id']} [{e.get('failureLayer')}] {str(e['change'])[:50]}")
                lines.append(f"      {e['reason']}")
            if r["failuresByLayer"]:
                lines += ["", "failures by harness layer:"]
                for layer, ids in r["failuresByLayer"].items():
                    lines.append(f"  {layer}: {', '.join(ids)}")
            lines += ["", f"RESEARCH NODES ({len(r['research'])}):"]
            for e in r["research"]:
                lines.append(f"  {e['id']}: {str(e['change'])[:60]}")
            lines += ["", "coverage: " + json.dumps(r["coverage"], ensure_ascii=False)]
            if r["linksWithoutQuote"]:
                lines.append("links with no stored quote (uncheckable): "
                             + ", ".join(r["linksWithoutQuote"]))
            lines += ["", "next questions the tree raises:"]
            for q in r["nextQuestions"]:
                lines.append(f"  - {q}")
            lines += ["", r["note"]]
            return text_response("kaggle_sources review", 0, "\n".join(lines), "")

        if action == "stats":
            s = srclib.stats()
            lines = [f"{s['count']} source(s) in {s['path']}",
                     "by kind: " + (", ".join(f"{k}={v}" for k, v in s["byKind"].items()) or "(none)"),
                     "by licence: " + (", ".join(f"{k}={v}" for k, v in s["byLicence"].items()) or "(none)"),
                     f"missing licence: {s['missingLicence']}",
                     f"no stored extract: {s['withoutExtract']}", "", s["note"]]
            return text_response("kaggle_sources stats", 0, "\n".join(lines), "")

        return text_response(
            "kaggle_sources", 2, "",
            f"unknown action: {action} (use add, extract, link, unlink, get, list, search, "
            "backlink, coverage, review, stats, doctor or install)")

    return {
        "content": [{"type": "text", "text": f"unknown tool: {name}"}],
        "isError": True,
    }

def respond(req_id: Any, result: Any) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def error(req_id: Any, code: int, message: str) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}) + "\n")
    sys.stdout.flush()


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            error(None, -32700, "parse error: not JSON")
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            respond(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        elif method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            pass  # notifications carry no id; nothing to answer
        elif method == "ping":
            respond(req_id, {})
        elif method == "tools/list":
            respond(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            respond(req_id, tool_call(str(params.get("name", "")), params.get("arguments") or {}))
        elif req_id is None:
            pass  # any other notification
        else:
            error(req_id, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
