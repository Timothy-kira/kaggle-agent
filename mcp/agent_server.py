"""Self-locating entry point for the Kaggle Agent MCP server.

The Desktop host launches an MCP server with its own working directory and does not expand
``${PLUGIN_ROOT}`` in ``servers.mcp.json`` args, so the manifest cannot carry a reliable path
to this file. Instead the manifest runs a one-line bootstrap that locates the package and
calls :func:`serve`.

Local install and Marketplace install do not have the same shape. A local plugin sits one level
down (``<root>/kaggle-agent``). A Marketplace plugin is cached in a directory named after the
content hash, further down still (``<root>/plugin-cache/official/sha256-tree-<hash>``). A search
that only descends one level therefore finds the local install and nothing else, so this one
descends :data:`MAX_DEPTH` and recognises a directory by what it contains rather than by what
it is called - the hash directory name is not something a plugin can rely on.

Identity is read from the package's own manifest. Matching the directory name would be both
impossible (it is a hash) and unsafe (any sibling directory could claim the name), so a package
counts as ours only when its ``.minimax-plugin/plugin.json`` names this plugin and its server
module is actually there.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from typing import Iterator, Optional

PLUGIN_NAME = "kaggle-agent"
MANIFEST = os.path.join(".minimax-plugin", "plugin.json")
SERVER_MODULE = "kaggle_server.py"
SERVER = os.path.join("mcp", SERVER_MODULE)
ENTRY = os.path.join("mcp", "agent_server.py")

PLUGIN_ROOTS = (
    # Local plugin source: <root>/kaggle-agent/
    os.path.join("~", ".minimax", "plugins"),
    os.path.join("~", ".mavis", "plugins"),
    # Marketplace: <root>/plugin-cache/official/sha256-tree-<hash>/ and imported packages.
    os.path.join("~", ".minimax", "v2", "plugin-cache"),
    os.path.join("~", ".minimax", "v2", "plugin-import"),
    os.path.join("~", ".mavis", "v2", "plugin-cache"),
    os.path.join("~", ".mavis", "v2", "plugin-import"),
)

MAX_DEPTH = 2
"""Levels descended under each root. One is enough for a local install; a Marketplace cache
needs three (``plugin-cache`` / ``official`` / ``sha256-tree-<hash>``) and two covers it."""


def is_package(directory: str) -> bool:
    """True when `directory` is this plugin's package root.

    Decided by the manifest's own ``name`` plus the presence of the server module, never by the
    directory's name. A directory that merely contains a file called ``agent_server.py`` is not
    this plugin and must not be loaded as if it were.
    """
    manifest = os.path.join(directory, MANIFEST)
    if not os.path.isfile(manifest) or not os.path.isfile(os.path.join(directory, SERVER)):
        return False
    try:
        with open(manifest, encoding="utf-8") as handle:
            return json.load(handle).get("name") == PLUGIN_NAME
    except (OSError, ValueError):
        # A malformed manifest is a package we decline to claim, not one that crashes the search.
        return False


def candidates(base: str) -> Iterator[str]:
    """`base`, then every directory beneath it, down to :data:`MAX_DEPTH` levels.

    Ordered, so a machine with two installed copies resolves to the same one on every run.
    """
    yield base
    frontier = [base]
    for _ in range(MAX_DEPTH):
        deeper = []
        for directory in frontier:
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in entries:
                child = os.path.join(directory, name)
                if os.path.isdir(child):
                    yield child
                    deeper.append(child)
        frontier = deeper


def locate() -> Optional[str]:
    """Absolute path of the package's ``mcp`` directory, found by manifest, or None."""
    for root in PLUGIN_ROOTS:
        base = os.path.expanduser(root)
        for directory in candidates(base):
            if is_package(directory):
                return os.path.join(directory, "mcp")
    # Last resort: this file's own package, when reached by a direct path rather than a search.
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(parent, "mcp") if is_package(parent) else None


def serve() -> int:
    mcp_dir = locate()
    # locate() returns the `mcp` directory itself, so this is the bare module name - not SERVER,
    # which is package-root relative and would ask for mcp/mcp/kaggle_server.py.
    server = os.path.join(mcp_dir, SERVER_MODULE) if mcp_dir else ""
    if not server or not os.path.isfile(server):
        sys.stderr.write(
            f"[kaggle-agent] cannot locate {SERVER}. Searched {PLUGIN_ROOTS} to {MAX_DEPTH} levels "
            f"deep for a package whose {MANIFEST} names {PLUGIN_NAME}.\n"
        )
        return 1
    if mcp_dir not in sys.path:
        sys.path.insert(0, mcp_dir)
    runpy.run_path(server, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
