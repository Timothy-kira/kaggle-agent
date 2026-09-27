"""Self-locating entry point for the Kaggle Agent MCP server.

The Desktop host launches an MCP server with its own working directory and does not expand
``${PLUGIN_ROOT}`` in ``servers.mcp.json`` args, so the manifest cannot carry a reliable path
to this file. Instead the manifest runs a one-line bootstrap that locates the package through
``~`` and calls :func:`serve`.

That bootstrap hardcodes a plugin directory name, which is fragile across a rename. This module
removes that fragility: :func:`locate` finds the real package by looking for the marker file
``mcp/kaggle_server.py`` under the standard local-plugin roots, regardless of the directory's
current name.
"""

from __future__ import annotations

import os
import runpy
import sys
from typing import Optional

MARKER = os.path.join("mcp", "kaggle_server.py")
PLUGIN_ROOTS = (
    os.path.join("~", ".minimax", "plugins"),
    os.path.join("~", ".mavis", "plugins"),
)


def locate() -> Optional[str]:
    """Absolute path of the package's ``mcp`` directory, found by marker, or None."""
    for root in PLUGIN_ROOTS:
        base = os.path.expanduser(root)
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for name in entries:
            mcp_dir = os.path.join(base, name, "mcp")
            if os.path.isfile(os.path.join(mcp_dir, "kaggle_server.py")):
                return mcp_dir
    # Last resort: this file's own directory, when reached by a direct path.
    here = os.path.dirname(os.path.abspath(__file__))
    return here if os.path.isfile(os.path.join(here, "kaggle_server.py")) else None


def serve() -> int:
    mcp_dir = locate()
    server = os.path.join(mcp_dir, "kaggle_server.py") if mcp_dir else ""
    if not server or not os.path.isfile(server):
        sys.stderr.write(f"[kaggle-agent] cannot locate kaggle_server.py (searched {PLUGIN_ROOTS})\n")
        return 1
    if mcp_dir not in sys.path:
        sys.path.insert(0, mcp_dir)
    runpy.run_path(server, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
